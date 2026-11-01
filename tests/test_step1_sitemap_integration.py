"""Actual TLS service redirects, CDN robots and layered gzip through the pinned client."""

import asyncio
import gzip
import ssl

import trustme

import geo_audit.connection as connection
import geo_audit.models as models
from geo_audit import SafeHttpClient, validate_site
from geo_audit.handoff import prepare_step1
from geo_audit.step1 import Step1Config, Step1Runtime, load_step1_result
from tests.test_connection_integration import Resolver, allow_test_loopback, options
from tests.test_step1_sitemap import xml


def test_real_tls_cdn_maps_and_no_html_scope_expansion(monkeypatch, tmp_path):
    async def scenario():
        allow_test_loopback(monkeypatch)
        monkeypatch.setattr(models, "is_public_address", connection.is_public_address)
        ca = trustme.CA()
        cert = ca.issue_cert("site.test", "cdn.test")
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert.configure_cert(server_context)
        requests, sni, tasks = [], [], set()
        server_context.set_servername_callback(lambda _s, name, _c: sni.append(name))

        async def handler(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                requests.append(request)
                path = request.split(b" ")[1]
                status, extra = 200, ""
                if path == b"/robots.txt":
                    body = b"User-agent: *\nDisallow: /ru/private\n"
                    if b"Host: site.test:" in request:
                        body += f"Sitemap: https://cdn.test:{port}/map.xml.gz\n".encode()
                elif path == b"/map.xml.gz":
                    body = gzip.compress(gzip.compress(xml([base + "/ru/private"])))
                    extra = "Content-Encoding: gzip\r\n"
                else:
                    body = b"<html><title>Company</title><p>Company</p></html>"
                writer.write(
                    (
                        f"HTTP/1.1 {status} Test\r\nContent-Length: {len(body)}\r\n"
                        f"Content-Type: text/html\r\n{extra}\r\n"
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
            base = f"https://site.test:{port}"
            config = options(port, path_scope=("/ru/",))
            context = ssl.create_default_context()
            ca.configure_trust(context)
            client = SafeHttpClient(config, resolver=Resolver(), ssl_context=context)
            site = await validate_site(
                base + "/ru/", config, client, output_dir=tmp_path / "source"
            )
            runtime = Step1Runtime(
                prepare_step1(site),
                source_directory=site._run_directory,
                output_dir=tmp_path / "out",
                client=client,
                config=Step1Config(
                    source_step0_config_sha256=site.config_sha256,
                    allowed_sitemap_hosts=("cdn.test",),
                ),
            )
            async with runtime:
                found = await runtime.discover_urls()
                assert any(u.url.endswith("/ru/private") for u in found.urls)
                denied = await runtime.fetch_html(base + "/ru/private")
                assert denied.policy_denial.decision == "disallowed"
            if tasks:
                await asyncio.gather(*tasks)
        saved = load_step1_result(runtime.store.io.root)
        assert len(requests) == saved.run.budget.total_attempts == 4
        assert all(b"GET /ru/private " not in r for r in requests)
        assert all(r.startswith(b"GET ") for r in requests)
        assert sni == ["site.test", "site.test", "cdn.test", "cdn.test"]
        assert "cdn.test" not in runtime.scope.allowed_hosts
        assert saved.crawl.sitemaps[0].state == "parsed"

    asyncio.run(scenario())
