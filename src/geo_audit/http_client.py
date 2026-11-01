"""Sequential, reusable bounded GET client. No HTML classification or implicit crawling."""

import asyncio
import ssl
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal, cast, get_args

import anyio
import certifi
import httpcore
import httpx

from .body import LimitedBody
from .budget import Budget
from .config import Step0Config
from .connection import (
    CheckedEndpoint,
    PinnedBackend,
    PinnedTransport,
    Resolver,
    SystemResolver,
    check_endpoint,
)
from .errors import ErrorCode, ErrorDetail, GeoAuditError, problem
from .models import FetchAttempt, Header, RedirectHop, TLSState
from .policy import REDIRECT_STATUSES, choose_next_action, retry_after_seconds
from .psl import PublicSuffixList, load_bundled_psl
from .redaction import install_network_redaction, network_logging_scope
from .request_policy import RequestAuthorization, RequestPolicy, ResourcePurpose
from .scope import SiteScope, decide_redirect, scope_reason
from .timing import TimeServices
from .urls import NormalizedURL, normalize_input, resolve_location


@dataclass(frozen=True)
class HttpAttempt:
    attempt_id: str
    parent_attempt_id: str | None
    purpose: Literal["initial", "retry", "http_fallback", "redirect"]
    requested_url: str
    started_at: datetime
    elapsed_ms: float
    request_sent: bool
    target: CheckedEndpoint | None
    connected_ip: str | None
    tls_state: TLSState
    status_code: int | None
    response_headers: tuple[Header, ...]
    encoded_body: bytes
    decoded_body: bytes
    received_bytes: int
    body_complete: bool
    error: ErrorDetail | None
    resource_purpose: ResourcePurpose | None = None
    resource_headers: tuple[tuple[str, str], ...] = ()
    authorization: RequestAuthorization | None = None


@dataclass(frozen=True)
class FetchTrace:
    input_url: str
    attempts: tuple[HttpAttempt, ...]
    redirects: tuple[RedirectHop, ...]
    scope: SiteScope
    error: ErrorDetail | None
    fallback_used: bool
    downgraded: bool
    algorithm_version: Literal["step0-1.2", "step1-1.0"] = "step0-1.2"
    policy_denial: RequestAuthorization | None = None


HttpObserver = Callable[[HttpAttempt | RedirectHop], Awaitable[None]]


def _detail(code: ErrorCode, attempt_id: str | None = None) -> ErrorDetail:
    category = (
        "target"
        if code.startswith("TARGET_")
        else "content"
        if code.startswith("CONTENT_")
        else "budget"
        if code.startswith(("BUDGET_", "RETRY_"))
        else "http"
        if code.startswith("REDIRECT_")
        else "transport"
    )
    return ErrorDetail.model_validate(
        {
            "code": code,
            "category": category,
            "message": "HTTP operation stopped: " + code,
            "retryable": False,
            "attempt_id": attempt_id,
        }
    )


def _network_code(error: BaseException) -> ErrorCode:
    seen = set()
    cause: BaseException | None = error
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ssl.SSLCertVerificationError):
            return "HTTPS_TLS_CERTIFICATE_ERROR"
        if isinstance(cause, ssl.SSLError):
            return "HTTPS_TLS_HANDSHAKE_ERROR"
        cause = cause.__cause__ or cause.__context__
    if isinstance(error, (httpcore.TimeoutException, httpx.TimeoutException)):
        return "NETWORK_TIMEOUT"
    if isinstance(error, (httpcore.ConnectError, httpx.ConnectError)):
        return "NETWORK_CONNECT_ERROR"
    return "NETWORK_READ_ERROR"


def _expired_deadline() -> GeoAuditError:
    return GeoAuditError(_detail("BUDGET_DEADLINE"))


class SafeHttpClient:
    """Owns no persistent sockets/cookies; reuse this object and the same Budget for step 1.

    `resolver` is a trusted test/custom DNS seam; every returned IP is still validated.
    A proxy route is deliberately unsupported and fails before I/O.
    """

    def __init__(
        self,
        config: Step0Config,
        *,
        resolver: Resolver | None = None,
        psl: PublicSuffixList | None = None,
        ssl_context: ssl.SSLContext | None = None,
        proxy: str | None = None,
        timing: TimeServices | None = None,
    ):
        if proxy is not None:
            raise problem(
                "TARGET_SECURITY_UNAVAILABLE", "target", "Proxy egress pinning is not implemented."
            )
        if ssl_context is not None and (
            not ssl_context.check_hostname or ssl_context.verify_mode != ssl.CERT_REQUIRED
        ):
            raise problem(
                "TARGET_SECURITY_UNAVAILABLE", "target", "TLS verification must remain enabled."
            )
        self.config = Step0Config.model_validate_json(config.model_dump_json())
        self.resolver = resolver or SystemResolver()
        self.psl, self.ssl_context = psl or load_bundled_psl(), ssl_context
        self._last_started: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self.time = timing or TimeServices(sleep=asyncio.sleep)
        self.last_trace: FetchTrace | None = None
        install_network_redaction()

    def restore_pacing(self, attempts: tuple[FetchAttempt, ...]) -> None:
        """Conservatively seed a new process from completed step 0 observations.

        Use response completion rather than an unportable old monotonic timestamp.
        Existing live-client pacing can only be strengthened, never reset.
        """
        now, mono = self.time.utcnow(), self.time.monotonic()
        for attempt in attempts:
            if attempt.request_sent:
                host = normalize_input(attempt.requested_url).host_ascii
                end = attempt.started_at + timedelta(milliseconds=attempt.elapsed_ms)
                stamp = mono - max(0.0, (now - end).total_seconds())
                self._last_started[host] = max(self._last_started.get(host, stamp), stamp)

    async def get(
        self,
        url: str,
        *,
        scope: SiteScope,
        budget: Budget,
        step: Literal[0, 1] = 0,
        allow_http_fallback: bool = False,
        observer: HttpObserver | None = None,
        policy: RequestPolicy | None = None,
    ) -> FetchTrace:
        if budget.config != self.config:
            raise ValueError("client and shared budget must use the same configuration")
        if not scope.allowed_extra_ports.issubset(self.config.allowed_extra_ports):
            raise ValueError("scope ports must be explicitly enabled in configuration")
        if policy is not None and (step != 1 or allow_http_fallback):
            raise ValueError("resource policy requires step=1 and no HTTP fallback")
        if policy and policy.purpose == "robots":
            initial_robots = normalize_input(url)
            if initial_robots.url != initial_robots.origin + "/robots.txt":
                raise ValueError("robots entry must be the origin robots.txt")
            if initial_robots.host_ascii not in scope.allowed_hosts | policy.allowed_sitemap_hosts:
                raise ValueError("robots origin must be an explicitly permitted resource host")
        original_scope = scope
        denial: RequestAuthorization | None = None
        attempts: list[HttpAttempt] = []
        hops: list[RedirectHop] = []
        fallback, downgraded = False, False
        error: ErrorDetail | None = None
        initial = current = normalize_input(url)
        visited = {current.url}
        failed_https: set[str] = set()
        retry_count = 0
        purpose: Literal["initial", "retry", "http_fallback", "redirect"] = "initial"
        deadline = asyncio.timeout(budget.remaining_seconds(step=step))
        try:
            async with deadline:
                while True:
                    request_scope = scope
                    if policy and policy.purpose in {"robots", "sitemap"}:
                        hosts = (
                            frozenset({current.host_ascii})
                            if policy.purpose == "robots"
                            else original_scope.allowed_hosts | policy.allowed_sitemap_hosts
                        )
                        request_scope = SiteScope(hosts, (), original_scope.allowed_extra_ports)
                    reason = scope_reason(current, request_scope)
                    if reason:
                        error = _detail(reason)
                        break
                    if policy:
                        budget.check(step=step)
                    authorization = None
                    if policy and policy.authorize is not None:
                        authorization = await policy.authorize(current.url)
                        if authorization.decision != "allowed":
                            denial = authorization
                            break
                    async with self._lock:
                        await self._fetch_once(
                            current,
                            budget,
                            step,
                            attempts,
                            purpose,
                            observer,
                            policy,
                            authorization.min_interval_seconds if authorization else 0.0,
                            authorization,
                        )
                    last = attempts[-1]
                    if budget.remaining_seconds(step=step) <= 0:
                        error = _detail("BUDGET_DEADLINE", last.attempt_id)
                        break
                    if last.error is None and last.status_code in REDIRECT_STATUSES:
                        locations = [h.value for h in last.response_headers if h.name == "location"]
                        redirect_scope = request_scope
                        if policy and policy.purpose == "robots" and len(locations) == 1:
                            try:
                                proposed = resolve_location(current.url, locations[0])
                                redirect_scope = SiteScope(
                                    frozenset({proposed.host_ascii}),
                                    (),
                                    original_scope.allowed_extra_ports,
                                )
                            except GeoAuditError:
                                pass  # decide_redirect records the malformed location below.
                        decision = decide_redirect(
                            source=current,
                            status_code=last.status_code,
                            location=locations[0] if len(locations) == 1 else None,
                            scope=redirect_scope,
                            psl=self.psl,
                            visited=frozenset(visited),
                            failed_https=frozenset(failed_https),
                            hops=len(hops),
                            max_hops=self.config.max_redirect_hops,
                        )
                        if policy and decision.action == "allow_www_alias":
                            decision = replace(
                                decision,
                                action="needs_input",
                                scope=original_scope,
                                reason_code="REDIRECT_EXTERNAL_HOST",
                                psl_sha256=None,
                            )
                        # Validate an external proposal too, but never issue its GET.
                        if (
                            policy is None
                            and decision.target is not None
                            and decision.action == "needs_input"
                        ):
                            try:
                                await check_endpoint(decision.target, self.resolver)
                            except GeoAuditError as blocked:
                                decision = replace(
                                    decision,
                                    action="reject",
                                    scope=scope,
                                    reason_code=blocked.detail.code,
                                    psl_sha256=None,
                                )
                        hops.append(
                            RedirectHop(
                                schema_version="1.2.0",
                                source_attempt_id=last.attempt_id,
                                source_url=current.url,
                                status_code=cast(
                                    Literal[301, 302, 303, 307, 308], last.status_code
                                ),
                                raw_location=locations[0] if len(locations) == 1 else None,
                                target_url=decision.target.url if decision.target else None,
                                decision=decision.action,
                                reason_code=decision.reason_code,
                                observed_at=self.time.utcnow(),
                                psl_sha256=decision.psl_sha256,
                            )
                        )
                        if observer is not None:
                            await observer(hops[-1])
                        if decision.action in {"reject", "needs_input"}:
                            error = _detail(
                                decision.reason_code or "REDIRECT_INVALID_LOCATION",
                                last.attempt_id,
                            )
                            break
                        if decision.target is None:
                            raise RuntimeError("Redirect decision omitted its destination")
                        current = decision.target
                        if policy is None:
                            scope = decision.scope
                        visited.add(current.url)
                        downgraded |= decision.downgrade
                        retry_count, purpose = 0, "redirect"
                        continue
                    available = (
                        budget.remaining_seconds(step=step) > 0
                        and budget.snapshot().total_attempts < self.config.total_max_attempts
                        and (
                            step == 1
                            or budget.snapshot().step0_attempts < self.config.step0_max_attempts
                        )
                    )
                    action = choose_next_action(
                        initial_url=initial.url,
                        current_url=current.url,
                        status_code=last.status_code,
                        error_code=last.error.code if last.error else None,
                        allow_fallback=allow_http_fallback and self.config.allow_http_fallback,
                        fallback_used=fallback,
                        retries_used=retry_count,
                        max_retries=self.config.max_retries,
                        budget_available=available,
                    )
                    if action.action == "retry":
                        delay = self.config.retry_delays_seconds[retry_count]
                        values = [h.value for h in last.response_headers if h.name == "retry-after"]
                        retry_after = retry_after_seconds(
                            values[0] if len(values) == 1 else None, now=self.time.utcnow()
                        )
                        delay = max(
                            delay + self.time.jitter(0, self.config.retry_jitter_seconds),
                            retry_after or 0.0,
                        )
                        if delay >= budget.remaining_seconds(step=step):
                            error = _detail("RETRY_DEFERRED", last.attempt_id)
                            break
                        await self.time.sleep(delay)
                        budget.check(step=step)
                        retry_count += 1
                        purpose = "retry"
                        continue
                    if action.action == "http_fallback":
                        failed_https.add(current.url)
                        current = normalize_input("http://" + initial.url[len("https://") :])
                        visited.add(current.url)
                        fallback, retry_count, purpose = True, 0, "http_fallback"
                        continue
                    error = last.error
                    if not available and (
                        last.error is not None or last.status_code in {408, 429, 500, 502, 503, 504}
                    ):
                        error = _detail(
                            "BUDGET_DEADLINE"
                            if budget.remaining_seconds(step=step) <= 0
                            else "BUDGET_ATTEMPTS",
                            last.attempt_id,
                        )
                    break
        except TimeoutError:
            if not deadline.expired():
                raise  # An observer/resolver programming defect is not site unavailability.
            error = _detail("BUDGET_DEADLINE", attempts[-1].attempt_id if attempts else None)
        except GeoAuditError as caught:
            error = caught.detail
        except asyncio.CancelledError:
            self.last_trace = FetchTrace(
                url,
                tuple(attempts),
                tuple(hops),
                scope,
                _detail("NETWORK_CANCELLED", attempts[-1].attempt_id if attempts else None),
                fallback,
                downgraded,
                "step1-1.0" if policy else "step0-1.2",
                denial,
            )
            raise
        # Explicit cancellation propagates to the caller; _fetch_once still closes all resources.
        self.last_trace = FetchTrace(
            url,
            tuple(attempts),
            tuple(hops),
            scope,
            error,
            fallback,
            downgraded,
            "step1-1.0" if policy else "step0-1.2",
            denial,
        )
        return self.last_trace

    async def _fetch_once(
        self,
        url: NormalizedURL,
        budget: Budget,
        step: Literal[0, 1],
        attempts: list[HttpAttempt],
        purpose: Literal["initial", "retry", "http_fallback", "redirect"],
        observer: HttpObserver | None = None,
        policy: RequestPolicy | None = None,
        origin_interval: float = 0.0,
        authorization: RequestAuthorization | None = None,
    ) -> None:
        stamp, started = self.time.utcnow(), self.time.monotonic()
        attempt_id = "req-" + uuid.uuid4().hex
        parent = attempts[-1].attempt_id if attempts else None
        endpoint: CheckedEndpoint | None = None
        backend: PinnedBackend | None = None
        transport: PinnedTransport | None = None
        status: int | None = None
        headers: tuple[Header, ...] = ()
        resource_headers: tuple[tuple[str, str], ...] = ()
        body: LimitedBody | None = None
        sent, complete = False, False
        error: ErrorDetail | None = None
        defect = False
        log_scope = network_logging_scope.set(True)
        try:
            budget.check(step=step)
            endpoint = await check_endpoint(url, self.resolver)
            delay = max(self.config.min_host_interval_seconds, origin_interval) - (
                self.time.monotonic() - self._last_started.get(url.host_ascii, float("-inf"))
            )
            if delay > 0:
                await self.time.sleep(delay)
            budget.reserve_attempt(step=step)
            self._last_started[url.host_ascii] = self.time.monotonic()
            sent = True
            backend = PinnedBackend(endpoint)
            if url.scheme == "https" and self.ssl_context is None:
                self.ssl_context = ssl.create_default_context(cafile=certifi.where())
            transport = PinnedTransport(backend, ssl_context=self.ssl_context)
            timeout = httpx.Timeout(
                connect=self.config.connect_timeout_seconds,
                read=self.config.read_timeout_seconds,
                write=self.config.write_timeout_seconds,
                pool=self.config.pool_timeout_seconds,
            )
            async with httpx.AsyncClient(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                timeout=timeout,
                headers={
                    "User-Agent": policy.user_agent
                    if policy and policy.user_agent
                    else self.config.user_agent,
                    "Accept-Language": self.config.accept_language,
                    "Accept-Encoding": "gzip, deflate",
                    "Connection": "close",
                },
            ) as client:
                async with client.stream("GET", url.url) as response:
                    status = response.status_code
                    allowed = get_args(Header.model_fields["name"].annotation)
                    headers = tuple(
                        Header.model_validate(
                            {
                                "name": name.decode("ascii").lower(),
                                "value": value.decode("latin-1"),
                                "redacted": False,
                            }
                        )
                        for name, value in response.headers.raw
                        if name.decode("ascii").lower() in allowed
                    )
                    # Runtime-only extension; persisted step0 Header/version gates stay unchanged.
                    resource_headers = tuple(
                        (name.decode("ascii").lower(), value.decode("latin-1"))
                        for name, value in response.headers.raw
                        if name.decode("ascii").lower() in (*allowed, "x-robots-tag")
                    )
                    encodings = response.headers.get_list("content-encoding")
                    body = LimitedBody(
                        ",".join(encodings),
                        min(self.config.max_encoded_bytes, policy.max_encoded_bytes)
                        if policy
                        else self.config.max_encoded_bytes,
                        min(self.config.max_decoded_bytes, policy.max_decoded_bytes)
                        if policy
                        else self.config.max_decoded_bytes,
                    )
                    async for data in response.aiter_raw():
                        if budget.remaining_seconds(step=step) <= 0:
                            raise _expired_deadline()
                        body.feed(data)
                    body.finish()
                    if budget.remaining_seconds(step=step) <= 0:
                        raise _expired_deadline()
                    complete = True
        except GeoAuditError as caught:
            error = caught.detail.model_copy(update={"attempt_id": attempt_id})
        except (
            httpcore.NetworkError,
            httpcore.TimeoutException,
            httpcore.ProtocolError,
            httpx.TransportError,
        ) as caught:
            error = _detail(_network_code(caught), attempt_id)
        except asyncio.CancelledError:
            error = _detail("NETWORK_CANCELLED", attempt_id)
            raise
        except BaseException:
            defect = True
            raise
        finally:
            try:
                if transport is not None:
                    with anyio.CancelScope(shield=True):
                        await transport.aclose()
            finally:
                network_logging_scope.reset(log_scope)
            tls_state: TLSState = "not_applicable" if url.scheme == "http" else "not_observed"
            if url.scheme == "https":
                if backend and any(s.tls_verified for s in backend.streams):
                    tls_state = "verified"
                elif error and error.code == "HTTPS_TLS_CERTIFICATE_ERROR":
                    tls_state = "certificate_error"
                elif error and error.code == "HTTPS_TLS_HANDSHAKE_ERROR":
                    tls_state = "handshake_error"
            if not defect:
                encoded = bytes(body.encoded) if body else b""
                decoded = (
                    encoded
                    if body and body.decoded is body.encoded
                    else bytes(body.decoded)
                    if body
                    else b""
                )
                attempts.append(
                    HttpAttempt(
                        attempt_id,
                        parent,
                        purpose,
                        url.url,
                        stamp,
                        (self.time.monotonic() - started) * 1000,
                        sent,
                        endpoint,
                        backend.connected_ip if backend else None,
                        tls_state,
                        status,
                        headers,
                        encoded,
                        decoded,
                        body.received_bytes if body else 0,
                        complete,
                        error,
                        policy.purpose if policy else None,
                        resource_headers,
                        authorization,
                    )
                )
            if not defect and observer is not None:
                with anyio.CancelScope(shield=True):
                    await observer(attempts[-1])
