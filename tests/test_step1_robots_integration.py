"""Actual TLS and connection journal prove that robots runs before HTML redirect targets."""

import asyncio
import ssl

import pytest
import trustme

import geo_audit.connection as connection
import geo_audit.models as models
from geo_audit import SafeHttpClient, validate_site
from geo_audit.handoff import prepare_step1
from geo_audit.step1 import Step1Runtime, load_step1_result
from tests.test_connection_integration import Resolver, allow_test_loopback, options


@pytest.mark.parametrize("case", ["cdn_forbids", "new_origin_unreachable"])
def test_real_robots_authorizer_and_tls_no_forbidden_html(monkeypatch, tmp_path, case):
    async def scenario():
        allow_test_loopback(monkeypatch)
        monkeypatch.setattr(models, "is_public_address", connection.is_public_address)
        ca = trustme.CA()
        cert = ca.issue_cert("site.test", "cdn.test", "other.test")
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert.configure_cert(server_context)
        requests, tasks, sni = [], set(), []
        server_context.set_servername_callback(lambda _s, name, _c: sni.append(name))

        async def handler(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                requests.append(request)
                path = request.split(b" ")[1]
                status, extra, body = 200, "", b"<html><p>Company</p></html>"
                if path == b"/robots.txt":
                    if b"Host: other.test:" in request:
                        status, body = 503, b"unavailable"
                    elif case == "cdn_forbids":
                        status, extra, body = (
                            302,
                            f"Location: https://cdn.test:{port}/rules\r\n",
                            b"",
                        )
                    else:
                        body = b"User-agent: *\nAllow: /\n"
                elif path == b"/rules":
                    body = b"User-agent: *\nDisallow: /ru/start\n"
                elif path == b"/ru/start":
                    status, extra, body = (
                        302,
                        f"Location: https://other.test:{port}/ru/blocked\r\n",
                        b"",
                    )
                writer.write(
                    (
                        f"HTTP/1.1 {status} Test\r\nContent-Length: {len(body)}\r\n"
                        f"Content-Type: text/plain\r\n{extra}\r\n"
                    ).encode()
                    + body
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)

        server = await asyncio.start_server(handler, "127.0.0.1", 0, ssl=server_context)
        async with server:
            port = server.sockets[0].getsockname()[1]
            cfg = options(port, allowed_hosts=("other.test",), path_scope=("/ru/",))
            context = ssl.create_default_context()
            ca.configure_trust(context)
            client = SafeHttpClient(cfg, resolver=Resolver(), ssl_context=context)
            base = f"https://site.test:{port}"
            site = await validate_site(base + "/ru/", cfg, client, output_dir=tmp_path / "source")
            runtime = Step1Runtime(
                prepare_step1(site),
                source_directory=site._run_directory,
                output_dir=tmp_path / "out",
                client=client,
            )
            async with runtime:
                trace = await runtime.fetch_html(base + "/ru/start")
                assert trace.policy_denial.decision == (
                    "disallowed" if case == "cdn_forbids" else "unknown"
                )
            if tasks:
                await asyncio.gather(*tasks)
            saved = load_step1_result(runtime.store.io.root)
            expected = 3 if case == "cdn_forbids" else 6
            assert len(requests) == saved.run.budget.total_attempts == expected
            assert all(b"GET /ru/blocked " not in request for request in requests)
            assert all(b"User-Agent: GEOAudit/0.1\r\n" in request for request in requests)
            if case == "cdn_forbids":
                assert all(b"GET /ru/start " not in request for request in requests)
                assert sni == ["site.test", "site.test", "cdn.test"]
                assert "cdn.test" not in runtime.scope.allowed_hosts
            else:
                assert len(runtime.robots.cache) == 2
                assert saved.crawl.robots[-1].state == "unreachable"

    asyncio.run(scenario())
