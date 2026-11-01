"""Verified shared-budget session with explicit orchestration and isolated publication."""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, TypeVar

import anyio

from ..api import FetchResult
from ..errors import GeoAuditError, problem
from ..evidence import MAX_FILE_BYTES, EvidenceStore
from ..handoff import Step1Handoff, load_site_result, load_step1_input, verify_step1_input
from ..http_client import FetchTrace, HttpAttempt, HttpObserver, SafeHttpClient
from ..models import RedirectHop
from ..request_policy import RequestAuthorization, RequestPolicy
from ..scope import SiteScope
from ..timing import TimeServices
from ..urls import normalize_input
from .config import Step1Config
from .models import (
    Candidate,
    ChecksResult,
    Coverage,
    CrawlResult,
    DiagnosticResult,
    DiscoverySource,
    Material,
    Page,
    RunResult,
    Step1Result,
    TechnicalCheck,
    UserMaterial,
)
from .storage import Step1Store, encoded_json

if TYPE_CHECKING:
    from .crawler import Crawler
    from .discovery import DiscoveryResult, DiscoveryService
    from .robots import RobotsService

T = TypeVar("T")


class Step1Runtime:
    def __init__(
        self,
        handoff: Step1Handoff,
        *,
        source_directory: Path,
        output_dir: Path,
        config: Step1Config | None = None,
        client: SafeHttpClient | None = None,
        timing: TimeServices | None = None,
    ):
        self.time = timing or (client.time if client else TimeServices())
        if client and (
            self.time.monotonic != client.time.monotonic or self.time.utcnow != client.time.utcnow
        ):
            raise ValueError("runtime and client must use the same clock")
        source = EvidenceStore(source_directory)
        site = handoff.contract.site
        self.budget = handoff.budget
        baseline, current = handoff.contract.budget, self.budget.snapshot()
        if (
            self.budget.config != site.config
            or current.started_at != baseline.started_at
            or current.total_deadline_at != baseline.total_deadline_at
            or current.step0_deadline_at != baseline.step0_deadline_at
            or current.step0_attempts != baseline.step0_attempts
            or current.total_attempts < baseline.total_attempts
            or current.elapsed_seconds < baseline.elapsed_seconds
        ):
            raise problem("INPUT_ARTIFACT_INVALID", "input", "Shared budget was replaced or reset.")
        if site._live_budget is not None and site._live_budget is not self.budget:
            raise problem("INPUT_ARTIFACT_INVALID", "input", "Live budget identity mismatch.")
        # A detached budget must have been restored from this exact snapshot.
        # After restore, monotonic time is authoritative; comparing it to a second
        # wall-clock sample introduces false failures on coarse/adjusted UTC clocks.
        if site._live_budget is None and self.budget._restored_from != baseline:
            raise problem(
                "INPUT_ARTIFACT_INVALID", "input", "Detached budget did not account for idle time."
            )
        if self.budget._clock != self.time.monotonic:
            raise ValueError("runtime and shared Budget must use the same monotonic clock")
        try:
            self.budget.check(step=1)
        except GeoAuditError:
            raise problem(
                "INPUT_ARTIFACT_INVALID", "input", "Shared handoff budget exhausted."
            ) from None
        if load_site_result(source_directory, verify_files=False) != site:
            raise problem("INPUT_ARTIFACT_INVALID", "input", "Handoff/source mismatch.")
        verify_step1_input(handoff.contract, source, now=self.time.utcnow())
        if handoff.fetch_result.response != handoff.contract.selected_response or (
            handoff.fetch_result.evidence != handoff.contract.selected_evidence
        ):
            raise problem("INPUT_ARTIFACT_INVALID", "input", "Selected response mismatch.")
        if site._fetch_result is not None and handoff.fetch_result is not site._fetch_result:
            raise problem("INPUT_ARTIFACT_INVALID", "input", "Live FetchResult identity mismatch.")
        assessment, record = handoff.fetch_result.assessment, handoff.contract.selected_evidence
        if (
            assessment.state != site.content_state
            or assessment.text != handoff.fetch_result.html_text
            or assessment.basis != record.classification_basis
            or assessment.confidence != record.classification_confidence
            or assessment.applied_encoding != record.applied_encoding
            or assessment.declared_encoding != record.declared_encoding
            or assessment.replacements != record.replacement_characters
            or assessment.sniffed != record.sniffed_html
            or assessment.diagnostic_allowed != site.diagnostic_allowed
        ):
            raise problem("INPUT_ARTIFACT_INVALID", "input", "Selected assessment mismatch.")
        representations = {
            str(f.representation): f for f in handoff.contract.selected_evidence.files
        }
        # Validate bytes from the supplied FetchResult, while retaining its object identity.
        for representation, value in (
            ("content_encoded", handoff.fetch_result.content_encoded),
            ("entity_bytes", handoff.fetch_result.entity_bytes),
            ("decoded_text", handoff.fetch_result.html_text.encode("utf-8")),
        ):
            if (
                representation not in representations
                or source.verified_bytes(
                    representations[representation],
                    limit=MAX_FILE_BYTES,
                )
                != value
            ):
                raise problem("INPUT_ARTIFACT_INVALID", "input", "Selected bytes mismatch.")
        self.config = config or Step1Config(source_step0_config_sha256=site.config_sha256)
        if self.config.source_step0_config_sha256 != site.config_sha256:
            raise problem("INPUT_ARTIFACT_INVALID", "input", "Source config hash mismatch.")
        self.client = client or SafeHttpClient(site.config, timing=self.time)
        if self.client.config != site.config:
            raise ValueError("client/source configurations differ")
        self.client.restore_pacing(site.attempts)
        self.entry: FetchResult = handoff.fetch_result
        self.handoff = handoff
        self.scope = SiteScope(
            frozenset(site.allowed_hosts),
            site.path_scope,
            frozenset(site.config.allowed_extra_ports),
        )
        self.started_at = self.time.utcnow()
        self.publication_id = "step1-" + uuid.uuid4().hex
        self.store = Step1Store(output_dir / self.publication_id, source, site)
        self.entry_headers: tuple[tuple[str, str], ...] = tuple(
            (h.name, h.value) for h in self.entry.response.response_headers
        )
        self.entry_headers_complete = False
        self.entry_header_evidence_id: str | None = None
        if site._live_budget is self.budget and client is not None and client.last_trace:
            for attempt in client.last_trace.attempts:
                if (
                    attempt.attempt_id == self.entry.response.attempt_id
                    and attempt.requested_url == self.entry.response.requested_url
                    and attempt.decoded_body == self.entry.entity_bytes
                    and attempt.status_code == self.entry.response.status_code
                ):
                    self.entry_headers = attempt.resource_headers
                    self.entry_headers_complete = bool(attempt.resource_headers)
                    header_record = self.store.write_evidence(
                        encoded_json(
                            dict(
                                source_attempt_id=attempt.attempt_id,
                                requested_url=attempt.requested_url,
                                headers=attempt.resource_headers,
                                status_code=attempt.status_code,
                                tls_state=attempt.tls_state,
                                captured_at=attempt.started_at.isoformat(),
                            )
                        ),
                        representation="attempt_observation",
                        complete=True,
                        source_evidence_id=self.store.entry_evidence_id,
                        transform_version="live-selected-headers-1",
                    )
                    self.entry_header_evidence_id = header_record.evidence_id
                    break
        self.cache_hits = 1  # Reused entry is an observation, never a new HTTP attempt.
        self.result: Step1Result | None = None
        self._entered = False
        self._finished = False
        self._pending_urls: set[str] = set()
        self._stop_reason: str | None = (
            "RETRY_DEFERRED" if "RETRY_DEFERRED" in site.reason_codes else None
        )
        self._robots: RobotsService | None = None
        self._discovery: DiscoveryService | None = None
        self._crawler: Crawler | None = None
        self.user_materials: tuple[UserMaterial, ...] = ()
        self.diagnostics: tuple[DiagnosticResult, ...] = ()
        self.extra_checks: tuple[TechnicalCheck, ...] = ()
        self.pipeline_completed = False

    async def crawl_pages(self) -> None:
        from .crawler import Crawler

        if self._crawler is not None:
            raise ValueError("crawler already started")
        self._crawler = Crawler(self)
        await self._crawler.run()

    @property
    def discovery(self) -> "DiscoveryService":
        from .discovery import DiscoveryService

        if self._discovery is None:
            self._discovery = DiscoveryService(self)
        return self._discovery

    async def discover_urls(self) -> "DiscoveryResult":
        return await self.discovery.discover()

    @property
    def robots(self) -> "RobotsService":
        from .robots import RobotsService

        if self._robots is None:
            self._robots = RobotsService(self)
        return self._robots

    async def fetch_html(self, url: str) -> FetchTrace:
        """Production HTML boundary: our robots authorization before every attempt/redirect."""

        async def authorizer(target: str) -> RequestAuthorization:
            if self._crawler:
                if target != url and target in self._crawler.effective:
                    return RequestAuthorization("disallowed", "HTML_CACHE_ALIAS")
                return await self._crawler.authorize(target)
            return await self.robots.authorize(target)

        return await self.fetch(url, RequestPolicy("html", authorizer))

    @classmethod
    def from_directory(
        cls,
        directory: Path,
        *,
        output_dir: Path,
        config: Step1Config | None = None,
        client: SafeHttpClient | None = None,
        timing: TimeServices | None = None,
        expected_config_hash: str | None = None,
    ) -> "Step1Runtime":
        clock = timing or (client.time if client else TimeServices())
        handoff = load_step1_input(
            directory, timing=clock, expected_config_hash=expected_config_hash
        )
        return cls(
            handoff,
            source_directory=directory,
            output_dir=output_dir,
            config=config,
            client=client,
            timing=clock,
        )

    def check_time(self) -> None:
        if self.budget.remaining_seconds(step=1) <= 0:
            raise problem("BUDGET_DEADLINE", "budget", "Shared deadline exhausted.")

    def bounded_phase(self, data: bytes, process: Callable[[bytes], T], *, max_bytes: int) -> T:
        """Bound synchronous parser input and reject results completed after the deadline.

        This does not claim preemption of a CPU call. XML/extraction callers must also
        bound nodes, depth and work inside their own implementations.
        """
        self.check_time()
        if max_bytes < 1 or len(data) > min(max_bytes, MAX_FILE_BYTES):
            raise problem("CONTENT_TOO_LARGE", "content", "Phase input exceeds its cap.")
        result = process(data)
        self.check_time()
        return result

    def note_cache_hit(self) -> None:
        self.check_time()
        self.cache_hits += 1

    async def fetch(
        self,
        url: str,
        policy: RequestPolicy,
        *,
        observer: HttpObserver | None = None,
    ) -> FetchTrace:
        if not self._entered or self._finished:
            raise ValueError("resource fetch requires an active session")
        if not policy.allowed_sitemap_hosts <= frozenset(self.config.allowed_sitemap_hosts):
            raise ValueError("service hosts must be explicitly configured")
        if policy.purpose == "ua_probe" and not self.config.ua_probes_enabled:
            raise ValueError("UA diagnostics are disabled")
        if policy.purpose in {"robots", "sitemap"}:
            cap = (
                self.config.max_robots_bytes
                if policy.purpose == "robots"
                else self.config.max_sitemap_bytes
            )
            policy = replace(
                policy,
                max_encoded_bytes=min(policy.max_encoded_bytes, cap),
                max_decoded_bytes=min(policy.max_decoded_bytes, cap),
            )
        normalized = normalize_input(url).url
        self._pending_urls.add(normalized)
        self.check_time()

        async def persist(event: HttpAttempt | RedirectHop) -> None:
            self.store.observe(event, policy.purpose)
            if observer:
                await observer(event)

        if self._stop_reason == "RETRY_DEFERRED":
            trace = FetchTrace(
                normalized,
                (),
                (),
                self.scope,
                problem(
                    "RETRY_DEFERRED", "budget", "Shared run stopped at Retry-After deadline."
                ).detail,
                False,
                False,
                "step1-1.0",
            )
        else:
            trace = await self.client.get(
                url,
                scope=self.scope,
                budget=self.budget,
                step=1,
                policy=policy,
                observer=persist,
            )
        if trace.error and trace.error.category == "output":
            raise GeoAuditError(trace.error)
        if trace.error and trace.error.category == "budget":
            self._stop_reason = trace.error.code
        if trace.error is None or trace.error.code not in {"BUDGET_DEADLINE", "NETWORK_CANCELLED"}:
            self._pending_urls.discard(normalized)
        self.store.write_evidence(
            encoded_json(
                dict(
                    operation_url=url,
                    fetch_state=(
                        "skipped_robots"
                        if trace.policy_denial.decision == "disallowed"
                        else "skipped_policy_unknown"
                    )
                    if trace.policy_denial
                    else "failed"
                    if trace.error
                    else "completed",
                    resource_purpose=policy.purpose,
                    resource_policy=dict(
                        max_encoded_bytes=min(
                            policy.max_encoded_bytes, self.client.config.max_encoded_bytes
                        ),
                        max_decoded_bytes=min(
                            policy.max_decoded_bytes, self.client.config.max_decoded_bytes
                        ),
                        user_agent=policy.user_agent or self.client.config.user_agent,
                    ),
                    algorithm_version=trace.algorithm_version,
                    attempt_ids=[a.attempt_id for a in trace.attempts],
                    error=trace.error.model_dump(mode="json") if trace.error else None,
                    policy_denial=asdict(trace.policy_denial) if trace.policy_denial else None,
                )
            ),
            representation="attempt_observation",
            complete=True,
        )
        self.store.checkpoint("running")
        return trace

    async def __aenter__(self) -> "Step1Runtime":
        if self._entered or self._finished:
            raise ValueError("session cannot be entered twice")
        self._entered = True
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if isinstance(error, GeoAuditError) and error.detail.category == "output":
            self._finished = True  # Storage failure cannot promise another JSON write.
            return
        if self._finished:
            return
        cancelled = isinstance(error, asyncio.CancelledError)
        reason = (
            "NETWORK_CANCELLED"
            if cancelled
            else error.detail.code
            if isinstance(error, GeoAuditError)
            else "IMPLEMENTATION_BUG"
            if error
            else "BUDGET_DEADLINE"
            if self.budget.remaining_seconds(step=1) <= 0
            else self._stop_reason or "SKELETON_ONLY"
        )
        try:
            with anyio.CancelScope(shield=True):
                self.finish(
                    reason,
                    cancelled=cancelled,
                    failed=error is not None
                    and not cancelled
                    and (
                        not isinstance(error, GeoAuditError)
                        or error.detail.category in {"input", "implementation"}
                    ),
                )
        except BaseException as storage_error:
            if error is None:
                raise
            # Do not replace a programming error/cancellation with cleanup failure.
            error.add_note(
                "Step 1 cleanup did not publish a result; inspect completed raw evidence."
            )
            if isinstance(storage_error, GeoAuditError):
                error.add_note("Cleanup error code: " + storage_error.detail.code)

    def finish(
        self, reason: str = "SKELETON_ONLY", *, cancelled: bool = False, failed: bool = False
    ) -> Step1Result:
        if self._finished:
            raise ValueError("session cannot be finalized twice")
        self._finished = True
        site = self.handoff.contract.site
        url = site.effective_url
        if url is None:
            raise ValueError("verified handoff lost its effective URL")
        envelope = dict(
            schema_version="step1-1.0.0",
            algorithm_version="step1-1.0",
            run_id=site.run_id,
            publication_id=self.publication_id,
            config_hash=self.config.fingerprint(),
            registry_version=self._robots.registry.registry_version
            if self._robots
            else "not_loaded",
            started_at=self.started_at,
            finished_at=self.time.utcnow(),
        )
        candidate = Candidate(
            candidate_id="candidate-00001",
            url=url,
            sources=(
                DiscoverySource(kind="entry", source_id=self.store.entry_evidence_id, occurrence=0),
            ),
            role="entry",
            role_basis="reused_step0",
            language=None,
            priority=0,
            ordinal=0,
            selected=True,
            state="fetched",
            reason_code="REUSED_EVIDENCE",
            alias_of=None,
        )
        page = Page.model_validate(
            dict(
                page_id="page-00001",
                candidate_id=candidate.candidate_id,
                url=url,
                effective_url=url,
                evidence_id=self.store.entry_evidence_id,
                title=None,
                headings=(),
                lists=(),
                tables=(),
                main_text=None,
                alternate_texts=(),
                extraction_method=None,
                extractor_version=None,
                extraction_reason="EXTRACTION_NOT_IMPLEMENTED",
                content_state=site.content_state,
                useful_candidate=False,
                supporting_page=False,
                business_relevance="unknown",
                business_quotes=(),
                directives=(),
                signals=(),
                header_capture_complete=False,
                limitations=("SKELETON_ONLY",),
            )
        )
        crawl_coverage = self._crawler.finalize(cancelled=cancelled) if self._crawler else None
        from .material import summarize

        material = (
            summarize(self._crawler.pages, self.user_materials)
            if self._crawler
            else Material(
                state="needs_description",
                can_start_step2=False,
                reason="EXTRACTION_NOT_IMPLEMENTED",
                useful_page_ids=(),
                supporting_page_ids=(),
                user_materials=(),
            )
        )
        limitations: list[str] = []
        if not self._crawler:
            limitations.append("SKELETON_ONLY")
        else:
            from .checks import check
            from .diagnostics import result as diagnostic_result

            for kind, enabled, check_kind in (
                ("browser", self.config.browser_enabled, "javascript"),
                ("ua_probe", self.config.ua_probes_enabled, "ua_comparison"),
            ):
                if not any(d.kind == kind for d in self.diagnostics):
                    diagnostic_status = "unknown" if enabled else "not_applicable"
                    diagnostic_reason = (
                        (
                            "SAFE_EGRESS_UNAVAILABLE"
                            if kind == "browser"
                            else reason
                            if reason.startswith(("BUDGET_", "RETRY_", "NETWORK_"))
                            else "NOT_EXECUTED"
                        )
                        if enabled
                        else "CONFIG_DISABLED"
                    )
                    self.diagnostics += (
                        diagnostic_result(
                            kind,
                            None,
                            diagnostic_status,
                            diagnostic_reason,
                            selection="Pipeline ended before optional phase",
                        ),
                    )
                    self.extra_checks += (
                        check(
                            self,
                            kind=check_kind,
                            status=diagnostic_status,
                            reason=diagnostic_reason,
                            observation="Optional phase not executed.",
                        ),
                    )

            for candidate in self._crawler.candidates.values():
                if candidate.selected and candidate.state not in {"fetched", "duplicate"}:
                    self.extra_checks += (
                        check(
                            self,
                            kind="indexing_directives",
                            status="unknown",
                            reason="PAGE_NOT_OBSERVED",
                            observation="No complete page read; no noindex conclusion.",
                            url=candidate.url,
                        ),
                    )
            if not self.pipeline_completed:
                limitations.append("PIPELINE_NOT_COMPLETED")
            if self._robots and any(
                d.product_token == self._robots.own_token and d.decision != "allowed"
                for d in self._robots.decisions.values()
            ):
                limitations.append("OWN_ROBOTS_RESTRICTED_OR_UNKNOWN")
            if self._discovery and (
                self._discovery.sitemap_truncated
                or any(
                    s.state in {"invalid", "incomplete", "failed"}
                    for s in self._discovery.observations
                )
            ):
                limitations.append("SITEMAP_DISCOVERY_LIMITED")
            if (
                self._crawler.truncated
                or self._crawler.discarded
                or (
                    self._discovery
                    and self._discovery.result
                    and self._discovery.result.discovery_truncated
                )
            ):
                limitations.append("DISCOVERY_TRUNCATED")
            if any(not p.header_capture_complete for p in self._crawler.pages):
                limitations.append("LEGACY_HEADERS_INCOMPLETE")
            if any(
                c.state
                in {
                    "failed",
                    "not_attempted_budget",
                    "cancelled",
                    "not_selected_limit",
                    "skipped_robots",
                    "skipped_policy_unknown",
                }
                for c in self._crawler.candidates.values()
            ):
                limitations.append("BOUNDED_OR_RESTRICTED_COVERAGE")
            if any(p.extraction_reason.startswith("EXTRACTION_") for p in self._crawler.pages):
                limitations.append("EXTRACTION_LIMITED")
            if any(d.status == "unknown" for d in self.diagnostics):
                limitations.append("ENABLED_DIAGNOSTIC_UNAVAILABLE")
            if any("METADATA_TRUNCATED" in p.limitations for p in self._crawler.pages):
                limitations.append("METADATA_TRUNCATED")
        status = (
            "failed"
            if failed
            else "partial"
            if cancelled or reason.startswith(("BUDGET_", "RETRY_"))
            else "needs_input"
            if self._crawler and not material.can_start_step2
            else "partial"
            if limitations
            else "ok"
        )
        result = Step1Result(
            source=self.store.source,
            config=self.config,
            evidence=tuple(self.store.records),
            crawl=CrawlResult.model_validate(
                dict(
                    **envelope,
                    candidates=tuple(self._crawler.candidates.values())
                    if self._crawler
                    else (candidate,),
                    pages=tuple(self._crawler.pages) if self._crawler else (page,),
                    robots=tuple(self._robots.observations) if self._robots else (),
                    robots_decisions=tuple(self._robots.decisions.values()) if self._robots else (),
                    sitemaps=tuple(self._discovery.observations) if self._discovery else (),
                    pending_external_urls=tuple(self._crawler.external) if self._crawler else (),
                    coverage=crawl_coverage
                    if crawl_coverage is not None
                    else Coverage(
                        discovered_urls=1,
                        selected_candidates=1,
                        fetch_successes=1,
                        unique_html_pages=1,
                        extracted_pages=0,
                        useful_pages=0,
                        skipped=0,
                        failed=0,
                        discarded_entries=0,
                        skip_examples=(),
                        skip_examples_truncated=False,
                        sitemap_truncated=self._discovery.sitemap_truncated
                        if self._discovery
                        else False,
                    ),
                    material=material,
                )
            ),
            checks=ChecksResult.model_validate(
                dict(
                    **envelope,
                    checks=(self._robots.checks() if self._robots else ())
                    + (tuple(self._crawler.checks) if self._crawler else ())
                    + self.extra_checks,
                    diagnostics=self.diagnostics,
                )
            ),
            run=RunResult.model_validate(
                dict(
                    **envelope,
                    status=status,
                    lifecycle="cancelled" if cancelled else "completed",
                    stop_reason=reason,
                    budget=self.budget.snapshot(),
                    browser_attempts=0,
                    cache_hits=self.cache_hits,
                    unfinished_urls=tuple(
                        sorted(
                            self._pending_urls
                            | {
                                c.url
                                for c in self._crawler.candidates.values()
                                if c.state
                                in {"cancelled", "not_selected_limit", "not_attempted_budget"}
                            }
                        )
                    )
                    if self._crawler
                    else tuple(sorted(self._pending_urls)),
                    limitations=tuple(limitations),
                )
            ),
        )
        self.store.publish(result)
        self.result = result
        return result
