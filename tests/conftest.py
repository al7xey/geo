"""Keep the Windows TLS-cancellation regression separate from subprocess tests."""

import asyncio
import errno
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
def macos_test_server_close(monkeypatch):
    if sys.platform != "darwin":
        return
    original = asyncio.start_server

    async def start_server(callback, *args, **kwargs):
        if kwargs.get("ssl") is None:
            return await original(callback, *args, **kwargs)

        async def handler(reader, writer):
            wait_closed = writer.wait_closed

            async def closing_server_writer():
                await wait_for_test_server_close(writer, wait_closed)

            writer.wait_closed = closing_server_writer
            await callback(reader, writer)

        return await original(handler, *args, **kwargs)

    monkeypatch.setattr(asyncio, "start_server", start_server)


async def wait_for_test_server_close(writer, wait_closed):
    try:
        await wait_closed()
    except ConnectionResetError as error:
        # Only an already-closing synthetic TLS server writer is covered.
        # Reads, response writes/assertions and all production clients are untouched.
        if not writer.is_closing() or error.errno != errno.ECONNRESET:
            raise
