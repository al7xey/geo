"""Pure normalization only; a normalized URL is NOT a checked destination."""

import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx
import idna

from .errors import ErrorCode, GeoAuditError, problem


def normalize_host(host: str) -> str:
    if len(host) > 253:
        raise ValueError("host exceeds the pre-IDNA length limit")
    if not host or any(
        unicodedata.category(c) in {"Cc", "Cf", "Cs"} or c.isspace() or c in "%/@\\?#[]"
        for c in host
    ):
        raise ValueError("invalid host")
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        if ":" in host:
            raise ValueError("invalid IPv6") from None
    host = idna.encode(host, uts46=True, transitional=False, std3_rules=True).decode().lower()
    host = host.removesuffix(".")
    if not host or len(host) > 253:
        raise ValueError("invalid host length")
    if re.fullmatch(r"[0-9]+|0x[0-9a-f]+", host.rsplit(".", 1)[-1]):
        return str(ipaddress.IPv4Address(host))
    return host


def validate_path(path: str, *, relative: bool = False) -> None:
    if re.search(r"%(?![0-9A-Fa-f]{2})", path):
        raise ValueError("invalid percent escape")
    if re.search(r"%(?:25|2f|5c|3f|23|0[0-9a-f]|1[0-9a-f]|7f)", path, re.I):
        raise ValueError("ambiguous encoded path delimiter/control/percent")
    for part in path.split("/"):
        if unquote(part) in {".", ".."} and (not relative or "%" in part):
            raise ValueError("ambiguous dot segment")


@dataclass(frozen=True)
class NormalizedURL:
    input_url: str
    url: str
    input_scheme_explicit: bool
    host_ascii: str
    host_unicode: str
    origin: str
    entry_path: str

    @property
    def scheme(self) -> str:
        return urlsplit(self.url).scheme

    @property
    def port(self) -> int:
        return urlsplit(self.url).port or (443 if self.scheme == "https" else 80)


def normalize_input(input_url: str) -> NormalizedURL:
    if len(input_url) > 16384:
        raise problem("INPUT_URL_INVALID", "input", "URL exceeds the 16 KiB input limit.")
    if any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in input_url):
        raise problem("INPUT_URL_INVALID", "input", "Control characters are forbidden.")
    raw = input_url.strip(" ")
    if not raw or "\\" in raw or any(c.isspace() for c in raw):
        raise problem("INPUT_URL_INVALID", "input", "Empty or ambiguous URL.")
    explicit = bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", raw))
    scheme_match = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*):", raw)
    if scheme_match and not explicit:
        if "." not in scheme_match[1] or not re.match(
            r"^[0-9]+(?:[/?#]|$)", raw[len(scheme_match[0]) :]
        ):
            code: ErrorCode = (
                "INPUT_URL_INVALID"
                if scheme_match[1].lower() in {"http", "https"}
                else "INPUT_SCHEME_UNSUPPORTED"
            )
            raise problem(code, "input", "Malformed or unsupported URL scheme.")
    if explicit and raw.split(":", 1)[0].lower() not in {"http", "https"}:
        raise problem("INPUT_SCHEME_UNSUPPORTED", "input", "Only HTTP and HTTPS are supported.")
    candidate = raw if explicit else "https://" + raw.removeprefix("//")
    try:
        parsed = urlsplit(candidate)
        if parsed.username is not None or parsed.password is not None:
            raise problem("INPUT_CREDENTIALS_FORBIDDEN", "input", "URL credentials are forbidden.")
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("invalid scheme or host")
        try:
            port = parsed.port
        except ValueError:
            raise problem("INPUT_PORT_INVALID", "input", "Port must be in 1..65535.") from None
        if port == 0 or parsed.netloc.endswith(":"):
            raise problem("INPUT_PORT_INVALID", "input", "Port must be in 1..65535.")
        host = normalize_host(parsed.hostname)
        if parsed.netloc.startswith("["):
            ipaddress.IPv6Address(parsed.hostname)
        # Reject alternate numeric IPv4 spellings that OS resolvers may interpret differently.
        if ":" not in host and all(
            re.fullmatch(r"[0-9]+|0[xX][0-9a-fA-F]+", label) for label in host.split(".")
        ):
            ipaddress.IPv4Address(host)
        authority = f"[{host}]" if ":" in host else host
        scheme = parsed.scheme.lower()
        if port and port != {"http": 80, "https": 443}[scheme]:
            authority += f":{port}"
        path = parsed.path or "/"
        validate_path(path)
        if re.search(r"%(?![0-9A-Fa-f]{2})", path + parsed.query):
            raise ValueError("invalid percent escape")
        path = quote(path, safe="/:@!$&'()*+,;=-._~%")
        query = quote(parsed.query, safe="/:@!$&'()*+,;=-._~%?")
        query_present = "?" in raw.split("#", 1)[0]
        url = urlunsplit((scheme, authority, path, query, ""))
        if not parsed.query and "?" in raw.split("#", 1)[0]:
            url += "?"
        # HTTPX may encode Unicode paths, but must not silently remove dot segments etc.
        parsed_httpx = httpx.URL(url)
        if parsed_httpx.raw_host.decode("ascii") != host:
            raise ValueError("parser host mismatch")
        encoded = str(parsed_httpx)
        expected_path = (path + ("?" + query if query_present else "")).encode("ascii")
        if parsed_httpx.raw_path != expected_path or encoded != url:
            raise ValueError("parser path mismatch")
        if "/./" in path or "/../" in path or path.endswith(("/.", "/..")):
            raise ValueError("ambiguous dot segments")
        display = host if ":" in host else idna.decode(host.encode())
        return NormalizedURL(
            input_url,
            encoded,
            explicit,
            host,
            display,
            f"{scheme}://{authority}",
            urlsplit(encoded).path,
        )
    except (ValueError, httpx.InvalidURL, idna.IDNAError):
        raise problem("INPUT_URL_INVALID", "input", "URL could not be parsed safely.") from None


def canonical_url(value: str) -> str:
    try:
        normalized = normalize_input(value)
    except GeoAuditError as error:
        raise ValueError(error.detail.code) from None
    if not normalized.input_scheme_explicit or normalized.url != value:
        raise ValueError("expected canonical absolute HTTP(S) URL without fragment")
    return value


def safe_display_url(value: str) -> str:
    """Console output never includes path, query, fragment or userinfo."""
    try:
        return normalize_input(value).origin
    except GeoAuditError:
        return "[invalid URL]"


def resolve_location(source_url: str, location: str) -> NormalizedURL:
    """RFC 3986 merge, preserving empty query and repeated slash segments."""
    try:
        source = normalize_input(source_url)
        raw = location.strip(" ")
        if (
            not raw
            or "\\" in raw
            or any(c.isspace() or unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in location)
        ):
            raise ValueError("invalid Location")
        ref, base = urlsplit(raw), urlsplit(source.url)
        if ref.scheme or raw.startswith("//"):
            return normalize_input(raw if ref.scheme else source.scheme + ":" + raw)
        validate_path(ref.path, relative=True)
        present = "?" in raw.split("#", 1)[0]
        if not ref.path:
            path = base.path
            query = ref.query if present else base.query
            present = present or "?" in source.url
        else:
            merged = (
                ref.path
                if ref.path.startswith("/")
                else base.path.rsplit("/", 1)[0] + "/" + ref.path
            )
            # RFC remove_dot_segments with empty segments retained (urljoin loses them).
            parts: list[str] = []
            for part in merged.split("/")[1:]:
                if part == "..":
                    if parts:
                        parts.pop()
                elif part != ".":
                    parts.append(part)
            if merged.endswith(("/.", "/..")):
                parts.append("")
            path, query = "/" + "/".join(parts), ref.query
        result = urlunsplit((source.scheme, base.netloc, path, query, ""))
        if present and not query:
            result += "?"
        return normalize_input(result)
    except (GeoAuditError, ValueError):
        raise problem(
            "REDIRECT_INVALID_LOCATION", "http", "Redirect Location is invalid."
        ) from None
