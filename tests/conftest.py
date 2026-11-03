"""Keep the Windows TLS-cancellation regression separate from subprocess tests."""

import asyncio
import sys

import pytest


@pytest.fixture(autouse=True)
def windows_tls_deadline_loop(request):
    if sys.platform != "win32" or request.node.name != (
        "test_tls_handshake_deadline_closes_real_tcp_stream"
    ):
        yield
        return
    # Selector supports the real socket/TLS deadline assertions in this test.
    # Other tests retain Proactor, which supports asyncio subprocesses on Windows.
    policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(policy)


@pytest.fixture(autouse=True)
def macos_test_server_close(request, monkeypatch):
    if sys.platform != "darwin" or request.node.name != (
        "test_real_tls_fifteen_step0_starts_then_step1_and_expired_handoff"
    ):
        return
    original = asyncio.start_server

    async def start_server(callback, *args, **kwargs):
        async def handler(reader, writer):
            wait_closed = writer.wait_closed

            async def closing_server_writer():
                try:
                    await wait_closed()
                except ConnectionResetError:
                    # macOS reports the client's forced TLS close as ECONNRESET.
                    # Only the synthetic server's already-closing writer is covered;
                    # request reading, assertions and all production clients are untouched.
                    if not writer.is_closing():
                        raise

            writer.wait_closed = closing_server_writer
            await callback(reader, writer)

        return await original(handler, *args, **kwargs)

    monkeypatch.setattr(asyncio, "start_server", start_server)
