"""Actual loopback TCP/TLS tests. Production public-only policy has no bypass flag.

Positive TLS cases patch ONLY the public-IP predicate in this test process to admit the
test server's loopback address. Peer pinning, TCP, SSL, SNI and hostname checks remain real.
"""

import asyncio
import ssl

import httpcore
import pytest
import trustme

import geo_audit.connection as connection
import geo_audit.http_client as client_module
from geo_audit import Budget, Step0Config
from geo_audit.connection import PinnedBackend
from geo_audit.http_client import SafeHttpClient
from geo_audit.psl import load_bundled_psl
from geo_audit.scope import build_scope
from geo_audit.urls import normalize_input


@pytest.fixture(scope="module")
def psl():
    return load_bundled_psl()


class Resolver:
    def __init__(self, addresses=("127.0.0.1",)):
        self.addresses, self.calls = addresses, 0

    async def resolve(self, host, port):
        self.calls += 1
        return self.addresses


def allow_test_loopback(monkeypatch):
    production = connection.is_public_address
    monkeypatch.setattr(
        connection, "is_public_address", lambda ip: ip in {"127.0.0.1", "::1"} or production(ip)
    )


def options(port, **extra):
    return Step0Config(
        allowed_extra_ports=(port,),
        min_host_interval_seconds=0.001,
        retry_delays_seconds=(0.001, 0.001),
        retry_jitter_seconds=0.0,
        **extra,
    )


@pytest.mark.parametrize(
    "hostname,trusted,expected",
    [
        ("site.test", True, None),
        ("wrong.test", True, "HTTPS_TLS_CERTIFICATE_ERROR"),
        ("site.test", False, "HTTPS_TLS_CERTIFICATE_ERROR"),
    ],
)
@pytest.mark.parametrize("address", ["127.0.0.1", "::1"])
def test_real_tls_sni_host_and_certificate_validation(
    monkeypatch, psl, hostname, trusted, expected, address
):
    async def scenario():
        allow_test_loopback(monkeypatch)
        ca = trustme.CA()
        cert = ca.issue_cert("site.test")
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert.configure_cert(server_context)
        names, requests = [], []
        server_context.set_servername_callback(lambda _socket, name, _context: names.append(name))

        async def handler(reader, writer):
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                requests.append(request)
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello")
                await writer.drain()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, ssl.SSLError):
                    pass

        server = await asyncio.start_server(handler, address, 0, ssl=server_context)
        async with server:
            port = server.sockets[0].getsockname()[1]
            context = ssl.create_default_context()
            if trusted:
                ca.configure_trust(context)
            cfg = options(port)
            client = SafeHttpClient(
                cfg, psl=psl, resolver=Resolver((address,)), ssl_context=context
            )
            url = f"https://{hostname}:{port}/Services?x=1&x=2"
            result = await client.get(
                url,
                scope=build_scope(normalize_input(url), allowed_extra_ports=(port,)),
                budget=Budget(cfg),
            )
            assert (result.error.code if result.error else None) == expected
            assert names == [hostname]
            if expected is None:
                assert result.attempts[-1].decoded_body == b"hello"
                assert result.attempts[-1].connected_ip == address
                assert b"Host: site.test:" in requests[0]
                assert b"GET /Services?x=1&x=2 HTTP/1.1" in requests[0]
            else:
                assert requests == []  # TLS failure occurs before sending any HTTP request.

    asyncio.run(scenario())


def test_real_peer_mismatch_closed_before_http(monkeypatch, psl):
    async def scenario():
        received, closed = [], asyncio.Event()

        async def handler(reader, writer):
            try:
                received.append(await reader.read(1024))
            finally:
                writer.close()
                await writer.wait_closed()
                closed.set()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]

            class MisdirectedConnector(httpcore.AsyncNetworkBackend):
                async def connect_tcp(
                    self,
                    host,
                    requested_port,
                    timeout=None,
                    local_address=None,
                    socket_options=None,
                ):
                    assert (
                        host == "8.8.8.8"
                    )  # Backend received a numeric pinned address, never site.test.
                    return await httpcore.AnyIOBackend().connect_tcp(
                        "127.0.0.1", requested_port, timeout
                    )

            monkeypatch.setattr(
                client_module,
                "PinnedBackend",
                lambda endpoint: PinnedBackend(endpoint, MisdirectedConnector()),
            )
            cfg = options(port)
            url = f"http://site.test:{port}/"
            client = SafeHttpClient(cfg, psl=psl, resolver=Resolver(("8.8.8.8",)))
            result = await client.get(
                url,
                scope=build_scope(normalize_input(url), allowed_extra_ports=(port,)),
                budget=Budget(cfg),
            )
            assert result.error.code == "TARGET_DNS_REBINDING"
            await asyncio.wait_for(closed.wait(), 1)
            assert received == [b""]  # Real TCP accepted, then closed before HTTP bytes or TLS.

    asyncio.run(scenario())


def test_production_policy_never_connects_to_loopback(psl):
    async def scenario():
        hits = []

        async def handler(reader, writer):
            hits.append(True)
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            cfg = options(port)
            client = SafeHttpClient(cfg, psl=psl, resolver=Resolver())
            url = f"http://site.test:{port}/"
            budget = Budget(cfg)
            result = await client.get(
                url,
                scope=build_scope(normalize_input(url), allowed_extra_ports=(port,)),
                budget=budget,
            )
            assert result.error.code == "TARGET_NOT_PUBLIC"
            assert budget.snapshot().step0_attempts == 0 and hits == []

    asyncio.run(scenario())


def test_new_request_rechecks_dns_instead_of_reusing_socket(monkeypatch, psl):
    async def scenario():
        allow_test_loopback(monkeypatch)
        requests = []

        async def handler(reader, writer):
            try:
                requests.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nConnection: keep-alive\r\n\r\nx"
                )
                await writer.drain()
                assert await reader.read(1) == b""
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            resolver = Resolver()
            cfg = options(port)
            client, budget = SafeHttpClient(cfg, psl=psl, resolver=resolver), Budget(cfg)
            url = f"http://site.test:{port}/"
            scope = build_scope(normalize_input(url), allowed_extra_ports=(port,))
            assert (await client.get(url, scope=scope, budget=budget)).error is None
            resolver.addresses = ("10.0.0.1",)
            second = await client.get(url, scope=scope, budget=budget, step=1)
            assert second.error.code == "TARGET_NOT_PUBLIC"
            assert resolver.calls == 2 and len(requests) == 1
            assert budget.snapshot().total_attempts == 1

    asyncio.run(scenario())


def test_tls_handshake_deadline_closes_real_tcp_stream(monkeypatch, psl):
    async def scenario():
        allow_test_loopback(monkeypatch)
        closed = asyncio.Event()

        async def silent_tls_server(reader, writer):
            try:
                assert await reader.read(4096)  # Receive a real ClientHello, then stall handshake.
                assert await reader.read(4096) == b""
            finally:
                writer.close()
                await writer.wait_closed()
                closed.set()

        server = await asyncio.start_server(silent_tls_server, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            cfg = options(port, step0_deadline_seconds=0.15)
            url = f"https://site.test:{port}/"
            client, budget = SafeHttpClient(cfg, psl=psl, resolver=Resolver()), Budget(cfg)
            result = await client.get(
                url,
                scope=build_scope(normalize_input(url), allowed_extra_ports=(port,)),
                budget=budget,
            )
            assert result.error.code == "BUDGET_DEADLINE"
            await asyncio.wait_for(closed.wait(), 1)
            assert budget.snapshot().step0_attempts == 1

    asyncio.run(scenario())


def test_real_redirect_to_private_address_is_never_requested(monkeypatch, psl):
    async def scenario():
        allow_test_loopback(monkeypatch)
        hits = []

        async def handler(reader, writer):
            try:
                hits.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(
                    b"HTTP/1.1 302 Found\r\nLocation: http://10.0.0.1/metadata\r\n"
                    b"Content-Length: 0\r\n\r\n"
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            cfg, url = options(port), f"http://site.test:{port}/"
            budget, resolver = Budget(cfg), Resolver()
            result = await SafeHttpClient(cfg, psl=psl, resolver=resolver).get(
                url,
                scope=build_scope(normalize_input(url), allowed_extra_ports=(port,)),
                budget=budget,
            )
            assert result.error.code == "TARGET_NOT_PUBLIC"
            assert len(hits) == budget.snapshot().step0_attempts == 1
            assert resolver.calls == 1

    asyncio.run(scenario())
