"""Bounded incremental XML and gzip file decoding; never fetches from a parser."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from io import BytesIO
from typing import Protocol, cast

import defusedxml.ElementTree as safe_et  # type: ignore[import-untyped]
from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]

from ..body import LimitedBody
from ..errors import GeoAuditError
from ..urls import normalize_input
from .config import Step1Config
from .models import SitemapEntry

NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
MAX_XML_NODES = 100000  # Work cap, no new default in the already versioned config fingerprint.


class XMLNode(Protocol):
    tag: str
    text: str | None

    def clear(self) -> None: ...


# defusedxml has no py.typed; confine its untyped public API to this structural bridge.
_iterparse = cast(Callable[..., Iterator[tuple[str, XMLNode]]], safe_et.iterparse)


class XMLLimit(Exception):
    pass


@dataclass
class ParsedSitemap:
    kind: str | None = None
    entries: list[SitemapEntry] = field(default_factory=list)
    state: str = "parsed"
    reason: str | None = None
    truncated: bool = False


def unpack_sitemap(
    data: bytes, config: Step1Config, check_time: Callable[[], None]
) -> tuple[bytes, bool, str | None]:
    """The HTTP entity is already content-decoded. Decode one additional gzip file layer."""
    if not data.startswith(b"\x1f\x8b"):
        return data, True, None
    body = LimitedBody("gzip", config.max_sitemap_bytes, config.max_sitemap_bytes)
    try:
        for offset in range(0, len(data), 64 * 1024):
            check_time()
            body.feed(data[offset : offset + 64 * 1024])
        body.finish()
    except GeoAuditError as error:
        if error.detail.code not in {
            "CONTENT_TOO_LARGE",
            "CONTENT_INCOMPLETE",
            "CONTENT_ENCODING_UNSUPPORTED",
        }:
            raise
        return (
            bytes(body.decoded),
            False,
            "SITEMAP_GZIP_LIMIT"
            if error.detail.code == "CONTENT_TOO_LARGE"
            else "SITEMAP_GZIP_INVALID",
        )
    check_time()
    return bytes(body.decoded), True, None


def parse_sitemap(
    data: bytes, config: Step1Config, remaining_entries: int, check_time: Callable[[], None]
) -> ParsedSitemap:
    result = ParsedSitemap()
    if len(data) > config.max_sitemap_bytes:
        result.state, result.reason, result.truncated = "incomplete", "SITEMAP_BYTES_LIMIT", True
        return result
    stack: list[str] = []
    root: XMLNode | None = None
    namespace = ""
    raw_locs: list[str] = []
    metadata: list[tuple[str, str]] = []
    entry_issues: list[str] = []
    nodes = 0
    try:
        for event, element in _iterparse(
            BytesIO(data),
            events=("start", "end"),
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        ):
            if event == "start":
                nodes += 1
                if nodes % 128 == 0:
                    check_time()
                stack.append(element.tag)
                if len(stack) > config.max_xml_depth or nodes > MAX_XML_NODES:
                    raise XMLLimit("SITEMAP_XML_WORK_LIMIT")
                if root is None:
                    root = element
                    if element.tag.startswith("{"):
                        namespace, _, local = element.tag[1:].partition("}")
                    else:
                        local = element.tag
                    if namespace not in {"", NAMESPACE} or local not in {"urlset", "sitemapindex"}:
                        raise XMLLimit("SITEMAP_XML_ROOT_INVALID")
                    result.kind = local
                    if not namespace:
                        result.reason = "SITEMAP_NAMESPACE_MISSING"
                elif len(stack) == 2:
                    raw_locs, metadata, entry_issues = [], [], []
                elif len(stack) > 3 and stack[2].endswith("loc"):
                    entry_issues.append("SITEMAP_NESTED_LOC")
                continue
            prefix = "{" + namespace + "}" if namespace else ""
            expected = prefix + ("url" if result.kind == "urlset" else "sitemap")
            if len(stack) == 3 and stack[1] == expected and element.tag.startswith(prefix):
                local = element.tag[len(prefix) :]
                value = (element.text or "").strip()
                if local == "loc":
                    raw_locs.append(value)
                elif local in {"lastmod", "priority", "changefreq"}:
                    metadata.append((local, value[:1024]))
                    if len(value) > 1024:
                        entry_issues.append("SITEMAP_METADATA_TRUNCATED")
            if len(stack) == 2 and element.tag == expected:
                if len(result.entries) >= remaining_entries:
                    raise XMLLimit("SITEMAP_URL_LIMIT")
                issues = list(dict.fromkeys(entry_issues))
                url = None
                raw_loc = raw_locs[0] if raw_locs else ""
                if len(raw_locs) != 1 or "SITEMAP_NESTED_LOC" in issues:
                    issues.append("SITEMAP_LOC_INVALID")
                elif len(raw_loc) >= 2048 or not raw_loc.startswith(("https://", "http://")):
                    issues.append("SITEMAP_LOC_INVALID")
                else:
                    try:
                        normalized = normalize_input(raw_loc)
                        if len(normalized.url) >= 2048:
                            issues.append("SITEMAP_LOC_TOO_LONG")
                        else:
                            url = normalized.url
                    except GeoAuditError:
                        issues.append("SITEMAP_LOC_INVALID")
                result.entries.append(
                    SitemapEntry(
                        ordinal=len(result.entries),
                        raw_loc=raw_loc[:2048],
                        url=url,
                        state="accepted" if url else "skipped",
                        publisher_metadata=tuple(metadata),
                        issues=tuple(issues),
                    )
                )
            element.clear()
            if len(stack) == 2 and root is not None:
                root.clear()  # Release completed root children rather than retaining the whole map.
            stack.pop()
    except XMLLimit as error:
        result.reason = str(error)
        result.state = "invalid" if result.reason == "SITEMAP_XML_ROOT_INVALID" else "incomplete"
        result.truncated = result.state == "incomplete"
    except (safe_et.ParseError, DefusedXmlException):
        result.state, result.reason = "invalid", "SITEMAP_XML_INVALID"
    check_time()
    return result
