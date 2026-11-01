"""Real controlled TLS0→1→publication→fresh offline process; no mock consumer handoff."""

import asyncio
import ssl
import subprocess
import sys

import pytest
import trustme

import geo_audit.connection as connection
import geo_audit.models as models
from geo_audit import SafeHttpClient
from geo_audit.step1.config import Step1Config
from geo_audit.step1.handoff import load_step2_input
from geo_audit.step1.models import Step1Result
from geo_audit.step1.pipeline import audit_site
from tests.test_connection_integration import Resolver, allow_test_loopback, options
from tests.test_step1_pipeline import BUSINESS, html
from tests.test_step1_runtime import snapshot


@pytest.mark.parametrize("case", ["one_page", "disallowed", "partial", "user_description"])
def test_real_tls_full_pipeline_and_new_process_offline_loader(monkeypatch, tmp_path, case):
    async def scenario():
        allow_test_loopback(monkeypatch)
        monkeypatch.setattr(models, "is_public_address", connection.is_public_address)
        ca = trustme.CA()
        cert = ca.issue_cert("site.test")
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert.configure_cert(server_context)
        received, tasks = [], set()

        async def handler(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                received.append(request)
                path = request.split(b" ")[1]
                status = 200
                if path == b"/robots.txt":
                    body = (
                        b"User-agent: *\nDisallow: /ru/services"
                        if case == "disallowed"
                        else b"User-agent: *\nAllow: /"
                    )
                elif path == b"/ru/services":
                    status = 404 if case == "partial" else 200
                    body = html()
                else:
                    body = html(
                        "Hello" if case == "user_description" else BUSINESS,
                        links='<a href="/ru/services">Services</a>'
                        if case in {"disallowed", "partial"}
                        else "",
                    )
                writer.write(
                    (
                        f"HTTP/1.1 {status} Test\r\nContent-Length: {len(body)}\r\n"
                        "Content-Type: text/html; charset=utf-8\r\n\r\n"
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
            config = options(port, path_scope=("/ru/",))
            context = ssl.create_default_context()
            ca.configure_trust(context)
            client = SafeHttpClient(config, resolver=Resolver(), ssl_context=context)
            result = await audit_site(
                f"https://site.test:{port}/ru/",
                config=config,
                step1_config=Step1Config(
                    source_step0_config_sha256=config.fingerprint(), max_sitemap_files=0
                ),
                output_dir=tmp_path,
                client=client,
                description=BUSINESS if case == "user_description" else None,
            )
            assert isinstance(result, Step1Result)
            if tasks:
                await asyncio.gather(*tasks)
        source = tmp_path / result.source.run_id
        before = snapshot(source)
        directory = tmp_path / result.run.publication_id
        assert len(received) == result.run.budget.total_attempts
        assert sum(r.startswith(b"GET /ru/ HTTP") for r in received) == 1
        if case == "disallowed":
            assert not any(r.startswith(b"GET /ru/services ") for r in received)
            assert any(
                c.reason_code == "PAGE_NOT_OBSERVED" and c.status == "unknown"
                for c in result.checks.checks
            )
        assert result.run.status == ("partial" if case in {"disallowed", "partial"} else "ok")
        program = r"""
import socket,sys
from pathlib import Path
def forbidden(*a,**k): raise AssertionError('offline network/extraction')
socket.getaddrinfo=socket.create_connection=socket.socket.connect=forbidden
import trafilatura
trafilatura.extract=forbidden
from geo_audit.step1.handoff import read_corpus
handoff,corpus=read_corpus(Path(sys.argv[1]))
assert handoff.result.crawl.material.can_start_step2 and corpus
print('verified offline')
"""
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            program,
            str(directory),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode()
        assert b"verified offline" in stdout
        assert snapshot(source) == before
        assert load_step2_input(directory).source_commit_sha256

    asyncio.run(scenario())


def test_saved_handoff_cli_in_new_process_without_new_entry_get(monkeypatch, tmp_path):
    import json

    from geo_audit import validate_site

    async def scenario():
        allow_test_loopback(monkeypatch)
        monkeypatch.setattr(models, "is_public_address", connection.is_public_address)
        received, tasks = [], set()

        async def handler(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                received.append(request)
                body = (
                    b"User-agent: *\nAllow: /"
                    if request.startswith(b"GET /robots.txt ")
                    else html()
                )
                writer.write(
                    (
                        f"HTTP/1.1 200 Test\r\nContent-Length: {len(body)}\r\n"
                        "Content-Type: text/html; charset=utf-8\r\n\r\n"
                    ).encode()
                    + body
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            config = options(port, path_scope=("/ru/",))
            client = SafeHttpClient(config, resolver=Resolver())
            site = await validate_site(
                f"http://site.test:{port}/ru/", config, client, output_dir=tmp_path / "source"
            )
            before = snapshot(site._run_directory)
            settings = tmp_path / "step1.json"
            settings.write_text(
                Step1Config(
                    source_step0_config_sha256=site.config_sha256, max_sitemap_files=0
                ).model_dump_json(),
                encoding="utf8",
            )
            program = r"""
import sys,pytest
from geo_audit import connection,models
from tests.test_connection_integration import allow_test_loopback
with pytest.MonkeyPatch.context() as patch:
    allow_test_loopback(patch)
    patch.setattr(models,'is_public_address',connection.is_public_address)
    async def local_resolver(self,host,port):return ('127.0.0.1',)
    patch.setattr(connection.SystemResolver,'resolve',local_resolver)
    from geo_audit.cli import main
    raise SystemExit(main(['step1',sys.argv[1],'--config',sys.argv[2],'--output-dir',sys.argv[3]]))
"""
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                program,
                str(site._run_directory),
                str(settings),
                str(tmp_path / "out"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            assert process.returncode == 3, stderr.decode()
            summary = json.loads(stdout)
            assert summary["can_start_step2"] and summary["material"] == "limited"
            if tasks:
                await asyncio.gather(*tasks)
        assert len(received) == 2
        assert sum(r.startswith(b"GET /ru/ HTTP") for r in received) == 1
        assert snapshot(site._run_directory) == before

    asyncio.run(scenario())
