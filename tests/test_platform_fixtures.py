"""Platform test adapters must not hide failures before server shutdown."""

import asyncio
import errno
from types import SimpleNamespace

import pytest

from tests import conftest as platform_fixtures
from tests.conftest import wait_for_test_server_close


@pytest.mark.parametrize(
    "closing,code,accepted",
    [
        (True, errno.ECONNRESET, True),
        (False, errno.ECONNRESET, False),
        (True, errno.ETIMEDOUT, False),
    ],
)
def test_server_shutdown_only_accepts_expected_reset(closing, code, accepted):
    async def wait_closed():
        raise ConnectionResetError(code, "synthetic server close")

    async def scenario():
        call = wait_for_test_server_close(SimpleNamespace(is_closing=lambda: closing), wait_closed)
        if accepted:
            await call
        else:
            with pytest.raises(ConnectionResetError):
                await call

    asyncio.run(scenario())


@pytest.mark.parametrize("tls", [True, False])
def test_macos_adapter_only_wraps_accepted_tls_server_writers(monkeypatch, tls):
    events = []

    async def wait_closed():
        events.append("closed")
        raise ConnectionResetError(errno.ECONNRESET, "synthetic peer close")

    writer = SimpleNamespace(is_closing=lambda: True, wait_closed=wait_closed)

    async def original_server(callback, *args, **kwargs):
        events.append("accepted")
        await callback(None, writer)
        return "server"

    async def callback(reader, accepted_writer):
        assert accepted_writer is writer
        events.append("checked response")
        await accepted_writer.wait_closed()

    monkeypatch.setattr(platform_fixtures, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(asyncio, "start_server", original_server)
    platform_fixtures.macos_test_server_close.__wrapped__(monkeypatch)

    async def scenario():
        if tls:
            assert await asyncio.start_server(callback, ssl=object()) == "server"
        else:
            with pytest.raises(ConnectionResetError):
                await asyncio.start_server(callback)

    asyncio.run(scenario())
    assert events == ["accepted", "checked response", "closed"]
