"""Offline real HTTP Core parsing over a controlled byte-stream backend (not real TCP)."""

import asyncio
import gzip
import ssl
from collections import deque
from datetime import UTC, datetime

import httpcore
import pytest

import geo_audit.http_client as module
from geo_audit import Budget, GeoAuditError, Step0Config
from geo_audit.body import LimitedBody
from geo_audit.connection import PinnedBackend, is_public_address
from geo_audit.http_client import SafeHttpClient
from geo_audit.policy import retry_after_seconds
from geo_audit.psl import load_bundled_psl
from geo_audit.scope import build_scope
from geo_audit.urls import normalize_input


@pytest.fixture(scope="module")
def psl():
    return load_bundled_psl()


class Resolver:
    def __init__(self, answers=("8.8.8.8",)):
        self.answers, self.calls = answers, []

    async def resolve(self, host, port):
        self.calls.append((host, port))
        return self.answers


class Stream(httpcore.AsyncNetworkStream):
    def __init__(self, script, host, port):
        self.script, self.host, self.port = deque(script), host, port
        self.closed, self.writes, self.sni = False, [], None
        self.read_event = asyncio.Event()

    async def read(self, max_bytes, timeout=None):
        self.read_event.set()
        if not self.script:
            return b""
        value = self.script.popleft()
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, float):
            await asyncio.sleep(value)
            return await self.read(max_bytes, timeout)
        if len(value) > max_bytes:
            self.script.appendleft(value[max_bytes:])
        return value[:max_bytes]

    async def write(self, data, timeout=None):
        self.writes.append(data)

    async def aclose(self):
        self.closed = True

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        assert ssl_context.check_hostname and ssl_context.verify_mode == ssl.CERT_REQUIRED
        self.sni = server_hostname
        return self

    def get_extra_info(self, name):
        return (self.host, self.port) if name == "server_addr" else None


class Connector(httpcore.AsyncNetworkBackend):
    def __init__(self, responses):
        self.responses, self.streams, self.calls = deque(responses), [], []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.calls.append((host, port))
        stream = Stream(self.responses.popleft(), host, port)
        self.streams.append(stream)
        return stream


def response(status=200, body=b"hello", headers=b""):
    return [
        f"HTTP/1.1 {status} Test\r\nContent-Length: {len(body)}\r\n".encode()
        + headers
        + b"\r\n"
        + body
    ]


def config(**updates):
    return Step0Config(
        min_host_interval_seconds=0.001,
        retry_jitter_seconds=0.0,
        retry_delays_seconds=(0.001, 0.002),
        **updates,
    )


def setup_client(monkeypatch, psl, scripts, cfg=None, resolver=None):
    connector = Connector(scripts)
    monkeypatch.setattr(
        module, "PinnedBackend", lambda endpoint: PinnedBackend(endpoint, connector)
    )
    cfg = cfg or config()
    client = SafeHttpClient(cfg, psl=psl, resolver=resolver or Resolver())
    return client, Budget(cfg), connector


def run(client, budget, url="https://example.com/", **kwargs):
    return asyncio.run(
        client.get(url, scope=build_scope(normalize_input(url)), budget=budget, **kwargs)
    )


def test_redirect_get_count_host_sni_and_cookie_env_isolation(monkeypatch, psl):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("SSL_CERT_FILE", "nonexistent")
    client, budget, connector = setup_client(
        monkeypatch,
        psl,
        [
            response(302, b"", b"Location: /new?x=1&x=2\r\nSet-Cookie: secret=yes\r\n"),
            response(),
        ],
    )
    result = run(client, budget)
    assert result.error is None and len(result.attempts) == 2
    assert budget.snapshot().step0_attempts == len(connector.calls) == 2
    assert result.attempts[1].purpose == "redirect"
    assert all(s.closed and s.sni == "example.com" for s in connector.streams)
    assert b"Host: example.com\r\n" in b"".join(connector.streams[0].writes)
    assert b"Cookie:" not in b"".join(connector.streams[1].writes)
    assert result.attempts[1].decoded_body == b"hello"


@pytest.mark.parametrize(
    "location,expected",
    [
        ("http://127.0.0.1/metadata", "TARGET_NOT_PUBLIC"),
        ("https://[::ffff:127.0.0.1]/", "TARGET_NOT_PUBLIC"),
        ("https://other.com/", "REDIRECT_EXTERNAL_HOST"),
    ],
)
def test_no_get_to_forbidden_redirect(monkeypatch, psl, location, expected):
    client, budget, connector = setup_client(
        monkeypatch, psl, [response(302, b"", f"Location: {location}\r\n".encode())]
    )
    result = run(client, budget)
    assert result.error.code == expected
    assert len(connector.calls) == budget.snapshot().step0_attempts == 1
    assert all(s.closed for s in connector.streams)


def test_explicit_retries_and_attempt_limit(monkeypatch, psl):
    cfg = config(step0_max_attempts=2)
    client, budget, connector = setup_client(
        monkeypatch, psl, [response(503), response(503), response()], cfg
    )
    result = run(client, budget)
    assert result.error.code == "BUDGET_ATTEMPTS"
    assert len(connector.calls) == budget.snapshot().step0_attempts == 2
    assert [a.purpose for a in result.attempts] == ["initial", "retry"]


def test_retry_after_larger_than_deadline_is_not_ignored(monkeypatch, psl):
    client, budget, connector = setup_client(
        monkeypatch, psl, [response(429, headers=b"Retry-After: 1000\r\n")]
    )
    result = run(client, budget)
    assert result.error.code == "RETRY_DEFERRED" and len(connector.calls) == 1


@pytest.mark.parametrize(
    "value,expected", [("12", 12.0), ("-1", None), ("invalid", None), ("1.5", None)]
)
def test_retry_after_numeric(value, expected):
    assert retry_after_seconds(value, now=datetime.now(UTC)) == expected


def test_retry_after_http_date():
    now = datetime(2026, 11, 1, tzinfo=UTC)
    assert retry_after_seconds("Sun, 01 Nov 2026 00:00:15 GMT", now=now) == 15


@pytest.mark.parametrize(
    "encoding,body,limit,expected",
    [
        ("gzip", gzip.compress(b"x" * 1_000_000), 1024, "CONTENT_TOO_LARGE"),
        ("gzip", gzip.compress(b"hello")[:-3], 1024, "CONTENT_INCOMPLETE"),
        ("br", b"unknown", 1024, "CONTENT_ENCODING_UNSUPPORTED"),
    ],
)
def test_bounded_decompression_and_connection_closure(
    monkeypatch, psl, encoding, body, limit, expected
):
    cfg = config(max_decoded_bytes=limit)
    client, budget, connector = setup_client(
        monkeypatch,
        psl,
        [response(body=body, headers=f"Content-Encoding: {encoding}\r\n".encode())],
        cfg,
    )
    result = run(client, budget)
    assert result.error.code == expected
    assert len(result.attempts[-1].decoded_body) <= limit
    assert not result.attempts[-1].body_complete and all(s.closed for s in connector.streams)


def test_encoded_body_limit(monkeypatch, psl):
    client, budget, connector = setup_client(
        monkeypatch, psl, [response(body=b"x" * 20)], config(max_encoded_bytes=10)
    )
    result = run(client, budget)
    assert result.error.code == "CONTENT_TOO_LARGE"
    assert len(result.attempts[-1].encoded_body) <= 10 and connector.streams[0].closed


def test_slow_drip_exceeds_total_deadline_not_read_timeout(monkeypatch, psl):
    cfg = config(step0_deadline_seconds=0.08, read_timeout_seconds=5.0)
    script = [b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"] + [
        item for _ in range(100) for item in [0.02, b"1\r\nx\r\n"]
    ]
    client, budget, connector = setup_client(monkeypatch, psl, [script], cfg)
    result = run(client, budget)
    assert result.error.code == "BUDGET_DEADLINE"
    assert not result.attempts[-1].body_complete and connector.streams[0].closed
    assert result.attempts[-1].elapsed_ms < 1000


def test_explicit_cancel_closes_stream(monkeypatch, psl):
    async def scenario():
        client, budget, connector = setup_client(monkeypatch, psl, [[0.5, *response()]], config())
        task = asyncio.create_task(
            client.get(
                "http://example.com/",
                scope=build_scope(normalize_input("http://example.com/")),
                budget=budget,
            )
        )
        while not connector.streams:
            await asyncio.sleep(0)
        await connector.streams[0].read_event.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert connector.streams[0].closed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:10.0.0.1",
        "224.0.0.1",
        "ff02::1",
        "192.0.2.1",
    ],
)
def test_public_address_policy(ip):
    assert not is_public_address(ip)


def test_proxy_and_unverified_tls_fail_closed(psl):
    with pytest.raises(GeoAuditError):
        SafeHttpClient(config(), psl=psl, proxy="http://proxy.example/")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with pytest.raises(GeoAuditError):
        SafeHttpClient(config(), psl=psl, ssl_context=context)


def test_gzip_exact_boundary_and_multiple_members():
    body = LimitedBody("gzip", 100, 5)
    body.feed(gzip.compress(b"hello"))
    body.finish()
    assert body.decoded == b"hello"
    with pytest.raises(GeoAuditError):
        body.feed(gzip.compress(b"second"))


def test_algorithm_default_limits():
    cfg = Step0Config()
    assert (
        cfg.step0_deadline_seconds,
        cfg.step0_max_attempts,
        cfg.total_deadline_seconds,
        cfg.total_max_attempts,
    ) == (60, 15, 300, 150)


def test_fifteen_attempts_include_retries(monkeypatch, psl):
    cfg = Step0Config(
        max_retries=20,
        retry_delays_seconds=(0.001,) * 20,
        min_host_interval_seconds=0.001,
        retry_jitter_seconds=0.0,
    )
    client, budget, connector = setup_client(
        monkeypatch, psl, [response(503)] * 15 + [response()], cfg
    )
    result = run(client, budget)
    assert result.error.code == "BUDGET_ATTEMPTS"
    assert len(connector.calls) == len(result.attempts) == budget.snapshot().step0_attempts == 15


def test_no_fallback_after_http_403(monkeypatch, psl):
    client, budget, connector = setup_client(monkeypatch, psl, [response(403)])
    result = run(client, budget, allow_http_fallback=True)
    assert not result.fallback_used and result.attempts[-1].status_code == 403
    assert len(connector.calls) == 1


def test_read_retries_then_single_fallback(monkeypatch, psl):
    scripts = [[httpcore.ReadError("synthetic failure")]] * 3 + [response()]
    client, budget, connector = setup_client(monkeypatch, psl, scripts)
    result = run(client, budget, allow_http_fallback=True)
    assert result.error is None and result.fallback_used and len(connector.calls) == 4
    assert [a.purpose for a in result.attempts] == ["initial", "retry", "retry", "http_fallback"]


def test_mixed_dns_answer_never_reaches_connector(monkeypatch, psl):
    client, budget, connector = setup_client(
        monkeypatch, psl, [], resolver=Resolver(("8.8.8.8", "10.0.0.1"))
    )
    result = run(client, budget, allow_http_fallback=True)
    assert result.error.code == "TARGET_NOT_PUBLIC" and not result.fallback_used
    assert connector.calls == [] and budget.snapshot().total_attempts == 0


def test_retry_after_delay_is_honored(monkeypatch, psl):
    delays = []

    async def record_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(module.asyncio, "sleep", record_sleep)
    client, budget, connector = setup_client(
        monkeypatch, psl, [response(503, headers=b"Retry-After: 1\r\n"), response()]
    )
    assert run(client, budget).error is None
    assert max(delays) >= 1.0 and len(connector.calls) == 2


def test_tls_fallback_cycle_never_retries_broken_https(monkeypatch, psl):
    async def cert_failure(self, *_args, **_kwargs):
        try:
            raise ssl.SSLCertVerificationError(1, "synthetic certificate failure")
        except ssl.SSLCertVerificationError as error:
            raise httpcore.ConnectError("TLS failed") from error

    monkeypatch.setattr(Stream, "start_tls", cert_failure)
    client, budget, connector = setup_client(
        monkeypatch, psl, [response(), response(302, b"", b"Location: https://example.com/\r\n")]
    )
    result = run(client, budget, allow_http_fallback=True)
    assert result.error.code == "FALLBACK_NO_USABLE_RESULT"
    assert result.fallback_used and len(connector.calls) == 2
    assert [a.purpose for a in result.attempts] == ["initial", "http_fallback"]
    assert result.attempts[0].error.code == "HTTPS_TLS_CERTIFICATE_ERROR"
    assert all(stream.closed for stream in connector.streams)
