"""Reusable offline fixtures; actual TCP/TLS tests remain separate."""

import asyncio
import ssl
from collections import deque
from datetime import UTC, datetime, timedelta

import httpcore

from geo_audit.connection import PinnedBackend
from geo_audit.timing import TimeServices


class VirtualTime:
    def __init__(self):
        self.seconds = 0.0
        self.epoch = datetime(2026, 11, 1, 12, tzinfo=UTC)
        self.delays = []

    def monotonic(self):
        return self.seconds

    def utcnow(self):
        return self.epoch + timedelta(seconds=self.seconds)

    async def sleep(self, delay):
        self.delays.append(delay)
        self.seconds += delay
        await asyncio.sleep(0)

    def services(self):
        return TimeServices(self.monotonic, self.utcnow, self.sleep, lambda _a, _b: 0.0)


class Resolver:
    def __init__(self, answers=("8.8.8.8",), failure=None):
        self.answers, self.failure, self.calls = answers, failure, []

    async def resolve(self, host, port):
        self.calls.append((host, port))
        if self.failure:
            raise self.failure
        return self.answers


class Stream(httpcore.AsyncNetworkStream):
    def __init__(self, script, host, port):
        self.script, self.host, self.port = deque(script), host, port
        self.closed, self.writes, self.sni = False, [], None

    async def read(self, max_bytes, timeout=None):
        if not self.script:
            return b""
        data = self.script.popleft()
        if isinstance(data, BaseException):
            raise data
        if isinstance(data, float):
            await asyncio.sleep(data)
            return await self.read(max_bytes, timeout)
        if len(data) > max_bytes:
            self.script.appendleft(data[max_bytes:])
        return data[:max_bytes]

    async def write(self, data, timeout=None):
        self.writes.append(data)

    async def aclose(self):
        self.closed = True

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        assert ssl_context.check_hostname
        self.sni = server_hostname
        if self.script and isinstance(self.script[0], ssl.SSLError):
            error = self.script.popleft()
            raise httpcore.ConnectError("controlled TLS failure") from error
        return self

    def get_extra_info(self, name):
        return (self.host, self.port) if name == "server_addr" else None


class Connector(httpcore.AsyncNetworkBackend):
    def __init__(self, scripts):
        self.scripts, self.streams, self.calls = deque(scripts), [], []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.calls.append((host, port))
        if not self.scripts:
            raise AssertionError("Unexpected network connection")
        stream = Stream(self.scripts.popleft(), host, port)
        self.streams.append(stream)
        return stream


def wire(
    body=b"<!doctype html><html><title>Company</title><p>Hello</p></html>",
    *,
    status=200,
    headers=b"Content-Type: text/html; charset=utf-8\r\n",
):
    return [
        f"HTTP/1.1 {status} Test\r\nContent-Length: {len(body)}\r\n".encode()
        + headers
        + b"\r\n"
        + body
    ]


def install_connector(monkeypatch, connector):
    import geo_audit.http_client as module

    monkeypatch.setattr(
        module, "PinnedBackend", lambda endpoint: PinnedBackend(endpoint, connector)
    )
