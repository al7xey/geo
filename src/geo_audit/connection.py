"""HTTPX transport backed by HTTP Core's public, IP-pinned network backend API."""

import asyncio
import ipaddress
import socket
import ssl
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import anyio
import certifi
import httpcore
import httpx

from .errors import ErrorCode, problem
from .urls import NormalizedURL


def is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        return is_public_address(str(mapped))
    if isinstance(address, ipaddress.IPv6Address) and (address.sixtofour or address.teredo):
        return False
    return (
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not getattr(address, "is_site_local", False)
        and str(address) != "168.63.129.16"
    )


@dataclass(frozen=True)
class CheckedEndpoint:
    host: str
    port: int
    addresses: tuple[str, ...]


class Resolver(Protocol):
    async def resolve(self, host: str, port: int) -> tuple[str, ...]: ...


class SystemResolver:
    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        try:
            ipaddress.ip_address(host)
            return (host,)
        except ValueError:
            pass
        try:
            entries = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
        except socket.gaierror as error:
            code: ErrorCode = (
                "DNS_NXDOMAIN"
                if error.errno
                in {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)}
                else "DNS_TEMPORARY"
            )
            raise problem(code, "transport", "DNS resolution failed.") from None
        return tuple(dict.fromkeys(str(entry[4][0]) for entry in entries))


async def check_endpoint(url: NormalizedURL, resolver: Resolver) -> CheckedEndpoint:
    # Literal addresses are never handed to an injected/system DNS resolver.
    addresses: tuple[str, ...]
    try:
        ipaddress.ip_address(url.host_ascii)
        addresses = (url.host_ascii,)
    except ValueError:
        addresses = await resolver.resolve(url.host_ascii, url.port)
    try:
        if not addresses or not all(is_public_address(ip) for ip in addresses):
            raise ValueError("non-public DNS answer")
        addresses = tuple(dict.fromkeys(ipaddress.ip_address(ip).compressed for ip in addresses))
    except ValueError:
        raise problem("TARGET_NOT_PUBLIC", "target", "Destination is not wholly public.") from None
    return CheckedEndpoint(url.host_ascii, url.port, addresses)


class _GuardedStream(httpcore.AsyncNetworkStream):
    def __init__(self, stream: httpcore.AsyncNetworkStream, hostname: str) -> None:
        self.stream, self.hostname = stream, hostname
        self.tls_verified = False

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return await self.stream.read(max_bytes, timeout)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        await self.stream.write(buffer, timeout)

    async def aclose(self) -> None:
        await self.stream.aclose()

    def get_extra_info(self, info: str) -> Any:
        return self.stream.get_extra_info(info)

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if (
            server_hostname != self.hostname
            or not ssl_context.check_hostname
            or ssl_context.verify_mode != ssl.CERT_REQUIRED
        ):
            await self.aclose()
            raise problem(
                "TARGET_SECURITY_UNAVAILABLE", "target", "TLS identity enforcement unavailable."
            )
        # TLS itself belongs entirely to the maintained AnyIO/Python SSL implementation.
        try:
            self.stream = await self.stream.start_tls(ssl_context, server_hostname, timeout)
            self.tls_verified = True
            return self
        except BaseException as error:
            with anyio.CancelScope(shield=True):
                await self.aclose()
            # Preserve classification before the pool can clear exception causes.
            cause: BaseException | None = error
            seen: set[int] = set()
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                if isinstance(cause, ssl.SSLCertVerificationError):
                    raise problem(
                        "HTTPS_TLS_CERTIFICATE_ERROR",
                        "transport",
                        "TLS certificate verification failed.",
                    ) from None
                if isinstance(cause, ssl.SSLError):
                    raise problem(
                        "HTTPS_TLS_HANDSHAKE_ERROR", "transport", "TLS handshake failed."
                    ) from None
                cause = cause.__cause__ or cause.__context__
            raise


class PinnedBackend(httpcore.AsyncNetworkBackend):
    """One checked connection only; all retries are explicit HTTP-client attempts."""

    def __init__(
        self, endpoint: CheckedEndpoint, connector: httpcore.AsyncNetworkBackend | None = None
    ):
        self.endpoint = endpoint
        self.connector = connector or httpcore.AnyIOBackend()
        self.connected_ip: str | None = None
        self.opened = False
        self.streams: list[_GuardedStream] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        target = self.endpoint
        if self.opened or (host, port) != (target.host, target.port) or local_address is not None:
            raise problem("TARGET_SECURITY_UNAVAILABLE", "target", "Unexpected connection route.")
        if not target.addresses or not all(is_public_address(ip) for ip in target.addresses):
            raise problem("TARGET_NOT_PUBLIC", "target", "Non-public connection destination.")
        self.opened = True
        ip = target.addresses[0]
        stream = await self.connector.connect_tcp(ip, port, timeout, None, socket_options)
        try:
            peer = stream.get_extra_info("server_addr")
            if (
                not peer
                or ipaddress.ip_address(peer[0]) != ipaddress.ip_address(ip)
                or peer[1] != port
            ):
                raise problem(
                    "TARGET_DNS_REBINDING",
                    "target",
                    "Connected peer differs from pinned destination.",
                )
            self.connected_ip = ip
            guarded = _GuardedStream(stream, target.host)
            self.streams.append(guarded)
            return guarded
        except BaseException:
            with anyio.CancelScope(shield=True):
                await stream.aclose()
            raise

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def aclose(self) -> None:
        with anyio.CancelScope(shield=True):
            for stream in self.streams:
                await stream.aclose()


class _ResponseStream(httpx.AsyncByteStream):
    def __init__(self, response: httpcore.Response):
        self.response = response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.response.aiter_stream():
            yield chunk

    async def aclose(self) -> None:
        await self.response.aclose()


class PinnedTransport(httpx.AsyncBaseTransport):
    def __init__(self, backend: PinnedBackend, *, ssl_context: ssl.SSLContext | None = None):
        context = ssl_context or ssl.create_default_context(cafile=certifi.where())
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise problem(
                "TARGET_SECURITY_UNAVAILABLE", "target", "TLS verification must remain enabled."
            )
        self.backend = backend
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=context,
            network_backend=backend,
            proxy=None,
            retries=0,
            max_connections=1,
            max_keepalive_connections=0,
            http1=True,
            http2=False,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET" or "sni_hostname" in request.extensions:
            raise problem(
                "TARGET_SECURITY_UNAVAILABLE", "target", "Unsupported request configuration."
            )
        core_request = httpcore.Request(
            method="GET",
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=b"",
            extensions=request.extensions,
        )
        response = await self.pool.handle_async_request(core_request)
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=_ResponseStream(response),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        with anyio.CancelScope(shield=True):
            try:
                await self.pool.aclose()
            finally:
                await self.backend.aclose()
