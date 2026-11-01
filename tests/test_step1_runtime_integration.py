"""Real controlled TCP/TLS, plus a fresh no-network consumer process."""

import asyncio
import ssl
import subprocess
import sys

import pytest
import trustme

import geo_audit.connection as connection
import geo_audit.models as models
from geo_audit import Budget, GeoAuditError, SafeHttpClient, TimeServices, validate_site
from geo_audit.handoff import prepare_step1
from geo_audit.request_policy import RequestAuthorization, RequestPolicy
from geo_audit.step1 import Step1Runtime, load_step1_result
from tests.fakes import VirtualTime
from tests.test_connection_integration import Resolver, allow_test_loopback, options
from tests.test_step1_runtime import snapshot


@pytest.mark.parametrize("case", ["complete", "cancel"])
def test_real_tls_resource_runtime_and_offline_new_process(monkeypatch, tmp_path, case):
    async def scenario():
        # Only fixture loopback admission is patched. Actual pin/peer/SNI/TLS remain active.
        allow_test_loopback(monkeypatch)
        monkeypatch.setattr(models, "is_public_address", connection.is_public_address)
        ca = trustme.CA()
        cert = ca.issue_cert("site.test", "cdn.test")
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert.configure_cert(server_context)
        sni, requests, tasks = [], [], set()
        server_context.set_servername_callback(lambda _s, name, _c: sni.append(name))
        slow_started, slow_closed = asyncio.Event(), asyncio.Event()

        async def handler(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                requests.append(request)
                path = request.split(b" ")[1]
                if path == b"/ru/slow":
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nx")
                    await writer.drain()
                    slow_started.set()
                    assert await asyncio.wait_for(reader.read(), 5) == b""
                    slow_closed.set()
                    return
                status, headers, body = (
                    200,
                    b"Content-Type: text/html\r\n",
                    b"<html><p>Company</p></html>",
                )
                if path == b"/robots.txt":
                    status, headers, body = (
                        302,
                        f"Location: https://cdn.test:{port}/policy\r\n".encode(),
                        b"",
                    )
                elif path == b"/policy":
                    headers, body = b"Content-Type: text/plain\r\n", b"User-agent: *\nAllow: /\n"
                elif path == b"/ru/retry":
                    status, headers, body = 503, b"Retry-After: 999\r\n", b"busy"
                writer.write(
                    (
                        f"HTTP/1.1 {status} Test\r\nContent-Length: {len(body)}\r\n"
                        "Connection: close\r\n"
                    ).encode()
                    + headers
                    + b"\r\n"
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
            cfg = options(port, path_scope=("/ru/",))
            context = ssl.create_default_context()
            ca.configure_trust(context)
            client = SafeHttpClient(cfg, resolver=Resolver(), ssl_context=context)
            base = f"https://site.test:{port}"
            site = await validate_site(base + "/ru/", cfg, client, output_dir=tmp_path / "source")
            before = snapshot(site._run_directory)
            ready = prepare_step1(site)
            runtime = Step1Runtime(
                ready,
                source_directory=site._run_directory,
                output_dir=tmp_path / "out",
                client=client,
            )

            async def permitted(url):
                return RequestAuthorization("allowed", "FIXTURE_POLICY")

            async def run():
                async with runtime:
                    robots = await runtime.fetch(base + "/robots.txt", RequestPolicy("robots"))
                    assert robots.error is None and len(robots.attempts) == 2
                    assert all(
                        a.connected_ip == "127.0.0.1" and a.tls_state == "verified"
                        for a in robots.attempts
                    )
                    assert (
                        robots.scope == runtime.scope
                        and "cdn.test" not in runtime.scope.allowed_hosts
                    )
                    if case == "cancel":

                        async def cancel_when_started():
                            await slow_started.wait()
                            task.cancel()

                        canceller = asyncio.create_task(cancel_when_started())
                        try:
                            await runtime.fetch(base + "/ru/slow", RequestPolicy("html", permitted))
                        finally:
                            await canceller
                    else:
                        trace = await runtime.fetch(
                            base + "/ru/retry", RequestPolicy("html", permitted)
                        )
                        assert trace.error.code == "RETRY_DEFERRED" and len(trace.attempts) == 1

            task = asyncio.create_task(run())
            if case == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await task
                await asyncio.wait_for(slow_closed.wait(), 5)
            else:
                await task
            if tasks:
                await asyncio.gather(*tasks)
            result = load_step1_result(runtime.store.io.root)
            assert len(requests) == result.run.budget.total_attempts == 4
            assert result.run.budget.step0_attempts == 1 and snapshot(site._run_directory) == before
            assert result.run.lifecycle == ("cancelled" if case == "cancel" else "completed")
            assert sni == ["site.test", "site.test", "cdn.test", "site.test"]
            assert requests[0].startswith(b"GET /ru/ ")
            assert all(b"Host: " in r for r in requests)
            program = r"""
import socket, sys
from pathlib import Path
from geo_audit.step1 import load_step1_result
def forbidden(*args, **kwargs):
    raise AssertionError('network in publication reader')
socket.getaddrinfo = socket.create_connection = socket.socket.connect = forbidden
result = load_step1_result(Path(sys.argv[1]))
assert result.run.budget.total_attempts == 4
assert result.run.status == 'partial'
print(result.run.lifecycle)
"""
            consumer = subprocess.run(
                [sys.executable, "-c", program, str(runtime.store.io.root)],
                capture_output=True,
                text=True,
                check=True,
            )
            assert consumer.stdout.strip() == result.run.lifecycle
            assert len(requests) == 4

    asyncio.run(scenario())


def test_real_tls_fifteen_step0_starts_then_step1_and_expired_handoff(monkeypatch, tmp_path):
    async def scenario():
        allow_test_loopback(monkeypatch)
        monkeypatch.setattr(models, "is_public_address", connection.is_public_address)
        ca = trustme.CA()
        cert = ca.issue_cert("site.test")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert.configure_cert(context)
        requests, tasks = [], set()
        statuses = [503, 503, 302] * 4 + [503, 302, 200]

        async def handler(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                requests.append(await reader.readuntil(b"\r\n\r\n"))
                index = len(requests) - 1
                status = statuses[index] if index < len(statuses) else 200
                headers = f"Location: /ru/step{index}\r\n" if status == 302 else ""
                body = b"<html><p>Company material</p></html>"
                writer.write(
                    f"HTTP/1.1 {status} Test\r\nContent-Length: {len(body)}\r\n"
                    f"Content-Type: text/html\r\n{headers}\r\n".encode()
                    + body
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)

        server = await asyncio.start_server(handler, "127.0.0.1", 0, ssl=context)
        async with server:
            port = server.sockets[0].getsockname()[1]
            cfg = options(port, path_scope=("/ru/",))
            trusted = ssl.create_default_context()
            ca.configure_trust(trusted)
            clock = VirtualTime()
            budget = Budget(cfg, timing=clock.services())
            client = SafeHttpClient(
                cfg, resolver=Resolver(), ssl_context=trusted, timing=clock.services()
            )
            base = f"https://site.test:{port}"
            site = await validate_site(
                base + "/ru/",
                cfg,
                client,
                budget,
                timing=clock.services(),
                output_dir=tmp_path / "source",
            )
            assert site.can_enter_step1 and len(requests) == 15
            clock.seconds = 61
            runtime = Step1Runtime(
                prepare_step1(site, timing=clock.services()),
                source_directory=site._run_directory,
                output_dir=tmp_path / "out",
                client=client,
                timing=clock.services(),
            )
            async with runtime:
                trace = await runtime.fetch(base + "/robots.txt", RequestPolicy("robots"))
                assert trace.error is None and trace.attempts[0].tls_state == "verified"
            assert runtime.result.run.budget.step0_attempts == 15
            assert runtime.result.run.budget.total_attempts == len(requests) == 16
            assert runtime.result.run.budget.total_deadline_at == site.budget.total_deadline_at
            future = TimeServices(utcnow=lambda: site.budget.total_deadline_at)
            with pytest.raises(GeoAuditError) as caught:
                Step1Runtime.from_directory(
                    site._run_directory, output_dir=tmp_path / "expired", timing=future
                )
            assert caught.value.detail.code == "INPUT_ARTIFACT_INVALID"
            assert not (tmp_path / "expired").exists() and len(requests) == 16
            if tasks:
                await asyncio.gather(*tasks)

    asyncio.run(scenario())
