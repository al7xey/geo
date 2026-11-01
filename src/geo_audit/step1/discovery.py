"""Entry links first, then policy-approved sitemaps, with one bounded discovery ledger."""

from __future__ import annotations

import uuid
from collections import deque
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ..contracts import Contract, Identifier
from ..errors import GeoAuditError
from ..models import URL
from ..request_policy import RequestPolicy
from ..scope import SiteScope, scope_reason
from ..urls import normalize_input, resolve_location
from .models import DiscoverySource, SitemapEntry, SitemapObservation
from .sitemap import parse_sitemap, unpack_sitemap
from .storage import encoded_json

if TYPE_CHECKING:
    from .runner import Step1Runtime


class DiscoveredURL(Contract):
    url: URL
    sources: tuple[DiscoverySource, ...]


class DiscoveryResult(Contract):
    schema_version: Literal["discovery-1.0.0"] = "discovery-1.0.0"
    urls: tuple[DiscoveredURL, ...] = Field(max_length=5000)
    sitemap_ids: tuple[Identifier, ...] = Field(max_length=5)
    problem_counts: tuple[tuple[str, int], ...]
    problem_examples: tuple[tuple[str, str], ...]
    examples_truncated: bool
    sitemap_truncated: bool
    discovery_truncated: bool
    skipped_sitemaps: int = Field(ge=0)
    unpacked_bytes: int = Field(ge=0)


class EntryLinks(HTMLParser):
    def __init__(self, limit: int):
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.base: str | None = None
        self.links: dict[str, int] = {}
        self.seen = 0
        self.truncated = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        hrefs = [value for key, value in attrs if key == "href" and value]
        if not hrefs:
            return
        if tag == "base" and self.base is None:
            self.base = hrefs[0]
        if tag == "a":
            self.seen += 1
            href = hrefs[0]
            if href not in self.links:
                if len(self.links) < self.limit and len(href) <= 16384:
                    self.links[href] = self.seen - 1
                else:
                    self.truncated = True


class DiscoveryService:
    def __init__(self, runtime: Step1Runtime):
        self.runtime = runtime
        self.urls: dict[str, list[DiscoverySource]] = {}
        self.observations: list[SitemapObservation] = []
        self._seen: set[str] = set()
        self._file_slots = 0
        self._entry_slots = 0
        self._unpacked = 0
        self._problems: dict[str, int] = {}
        self._examples: list[tuple[str, str]] = []
        self._examples_truncated = False
        self._truncated = False
        self._discovery_truncated = False
        self._skipped_files = 0
        self.result: DiscoveryResult | None = None
        self._started = False
        self._stopped = False
        self.external_urls: dict[str, None] = {}

    @property
    def sitemap_truncated(self) -> bool:
        return self._truncated

    def _problem(self, reason: str, raw: str) -> None:
        self._problems[reason] = self._problems.get(reason, 0) + 1
        if len(self._examples) < self.runtime.config.max_skip_examples:
            self._examples.append((reason, raw[:2048]))
        else:
            self._examples_truncated = True

    def _admit(self, url: str, source: DiscoverySource) -> None:
        normalized = normalize_input(url)
        reason = scope_reason(normalized, self.runtime.scope)
        if reason:
            if len(self.external_urls) < self.runtime.config.max_candidates:
                self.external_urls[normalized.url] = None
            self._problem(reason, url)
            return
        if normalized.url not in self.urls:
            if len(self.urls) >= self.runtime.config.max_candidates:
                self._problem("DISCOVERY_QUEUE_LIMIT", url)
                self._discovery_truncated = True
                return
            self.urls[normalized.url] = []
        if source not in self.urls[normalized.url]:
            self.urls[normalized.url].append(source)

    async def discover(self) -> DiscoveryResult:
        if self.result is not None:
            self.runtime.note_cache_hit()
            return self.result
        if self._started:
            raise ValueError("interrupted discovery cannot restart its quotas")
        self._started = True
        entry_url = self.runtime.handoff.contract.site.effective_url
        if entry_url is None:
            raise ValueError("verified entry URL missing")
        entry_id = self.runtime.store.entry_evidence_id
        self._admit(entry_url, DiscoverySource(kind="entry", source_id=entry_id, occurrence=0))
        links = EntryLinks(self.runtime.config.max_candidates)
        try:
            self.runtime.bounded_phase(
                self.runtime.entry.html_text.encode("utf-8"),
                lambda _: links.feed(self.runtime.entry.html_text),
                max_bytes=self.runtime.client.config.max_decoded_bytes * 4,
            )
            links.close()
        except AssertionError as error:
            if not str(error).startswith(("unknown status keyword ", "expected name token at ")):
                raise
            self._problem("HTML_LINK_PARSE_INCOMPLETE", entry_url)
        base = entry_url
        if links.base:
            try:
                base = resolve_location(entry_url, links.base).url
            except GeoAuditError:
                self._problem("HTML_BASE_INVALID", links.base)
        for href, ordinal in links.links.items():
            self.runtime.check_time()
            try:
                self._admit(
                    resolve_location(base, href).url,
                    DiscoverySource(
                        kind="html_link",
                        source_id="page-00001",
                        occurrence=ordinal,
                    ),
                )
            except GeoAuditError:
                self._problem("HTML_LINK_INVALID", href)
        if links.truncated:
            self._problem("HTML_LINK_SCAN_LIMIT", entry_url)
            self._discovery_truncated = True
        if self.runtime.config.max_sitemap_files:
            origin = normalize_input(entry_url).origin
            robots = await self.runtime.robots.get(origin)
            if robots.observation.state in {"present", "empty", "absent"}:
                directives = [
                    (
                        url,
                        1,
                        DiscoverySource(
                            kind="robots_directive",
                            source_id=robots.observation.observation_id,
                            occurrence=n,
                        ),
                    )
                    for n, url in enumerate(robots.observation.sitemap_urls)
                ]
                await self._drain(deque(directives))
                if not any(
                    source.kind == "sitemap" for sources in self.urls.values() for source in sources
                ):
                    for n, path in enumerate(("/sitemap.xml", "/sitemap_index.xml")):
                        if self._stopped:
                            break
                        if self._file_slots >= self.runtime.config.max_sitemap_files:
                            self._truncated = True
                            break
                        await self._drain(
                            deque(
                                [
                                    (
                                        origin + path,
                                        1,
                                        DiscoverySource(
                                            kind="standard_path",
                                            source_id=entry_id,
                                            occurrence=n,
                                        ),
                                    )
                                ]
                            )
                        )
                        if any(
                            s.kind == "sitemap" for sources in self.urls.values() for s in sources
                        ):
                            break
            else:
                self._problem("ROBOTS_POLICY_UNKNOWN", origin)
        self.result = DiscoveryResult(
            urls=tuple(
                DiscoveredURL(url=url, sources=tuple(sources)) for url, sources in self.urls.items()
            ),
            sitemap_ids=tuple(o.sitemap_id for o in self.observations),
            problem_counts=tuple(sorted(self._problems.items())),
            problem_examples=tuple(self._examples),
            examples_truncated=self._examples_truncated,
            sitemap_truncated=self._truncated,
            discovery_truncated=self._discovery_truncated,
            skipped_sitemaps=self._skipped_files,
            unpacked_bytes=self._unpacked,
        )
        self.runtime.store.write_evidence(
            encoded_json(self.result.model_dump(mode="json")),
            representation="discovery_observation",
            complete=True,
        )
        self.runtime.store.checkpoint("running")
        return self.result

    async def _drain(self, queue: deque[tuple[str, int, DiscoverySource]]) -> None:
        config = self.runtime.config
        service_scope = SiteScope(
            self.runtime.scope.allowed_hosts | frozenset(config.allowed_sitemap_hosts),
            (),
            self.runtime.scope.allowed_extra_ports,
        )
        while queue:
            self.runtime.check_time()
            raw, depth, source = queue.popleft()
            try:
                normalized = normalize_input(raw)
                if not normalized.input_scheme_explicit:
                    raise ValueError("relative sitemap")
            except (GeoAuditError, ValueError):
                self._problem("SITEMAP_URL_INVALID", raw)
                continue
            url = normalized.url
            if url in self._seen:
                self._problem("SITEMAP_DUPLICATE", url)
                continue
            self._seen.add(url)
            reason = scope_reason(normalized, service_scope)
            if reason or depth > config.max_sitemap_depth:
                self._skipped_files += 1
                self._problem("skipped_external_resource" if reason else "SITEMAP_DEPTH_LIMIT", url)
                self._truncated |= depth > config.max_sitemap_depth
                continue
            if (
                self._file_slots >= config.max_sitemap_files
                or self._entry_slots >= config.max_sitemap_urls
            ):
                self._skipped_files += len(queue) + 1
                self._problem("SITEMAP_FILE_OR_URL_LIMIT", url)
                self._truncated = True
                break
            self._file_slots += 1
            observation = await self._read(url, depth, source)
            self.observations.append(observation)
            if observation.reason_code in {"BUDGET_ATTEMPTS", "BUDGET_DEADLINE", "RETRY_DEFERRED"}:
                self._stopped = True
                self._truncated = True
                self._skipped_files += len(queue)
                return
            for entry in observation.entries:
                if entry.url is None:
                    self._problem("SITEMAP_LOC_INVALID", entry.raw_loc)
            accepted_entries = [entry for entry in observation.entries if entry.url is not None]
            for child, entry in zip(observation.child_sitemaps, accepted_entries, strict=False):
                queue.append(
                    (
                        child,
                        depth + 1,
                        DiscoverySource(
                            kind="sitemap",
                            source_id=observation.sitemap_id,
                            occurrence=entry.ordinal,
                        ),
                    )
                )
            for page, entry in zip(observation.urls, accepted_entries, strict=False):
                self._admit(
                    page,
                    DiscoverySource(
                        kind="sitemap", source_id=observation.sitemap_id, occurrence=entry.ordinal
                    ),
                )

    async def _read(self, url: str, depth: int, source: DiscoverySource) -> SitemapObservation:
        config = self.runtime.config
        trace = await self.runtime.fetch(
            url,
            RequestPolicy(
                "sitemap",
                self.runtime.robots.authorize,
                allowed_sitemap_hosts=frozenset(config.allowed_sitemap_hosts),
                max_encoded_bytes=config.max_sitemap_bytes,
                max_decoded_bytes=config.max_sitemap_bytes,
            ),
        )
        for attempt in trace.attempts:
            self._seen.add(attempt.requested_url)
        last = trace.attempts[-1] if trace.attempts else None
        ids = self.runtime.store.attempt_evidence.get(last.attempt_id, ()) if last else ()
        state, reason, truncated = "failed", None, False
        entries: tuple[SitemapEntry, ...] = ()
        pages: tuple[str, ...] = ()
        children: tuple[str, ...] = ()
        if trace.policy_denial:
            state, reason, ids = (
                "skipped",
                trace.policy_denial.reason_code,
                trace.policy_denial.evidence_ids,
            )
        elif trace.error or last is None or not last.body_complete or last.status_code == 206:
            state = "incomplete"
            reason = trace.error.code if trace.error else "SITEMAP_INCOMPLETE"
            truncated = bool(trace.error and trace.error.code == "CONTENT_TOO_LARGE")
        elif last.status_code in {404, 410}:
            state, reason = "absent", "SITEMAP_ABSENT"
        elif last.status_code != 200:
            reason = "SITEMAP_HTTP_ERROR"
        else:
            xml, complete, reason = unpack_sitemap(
                last.decoded_body, config, self.runtime.check_time
            )
            self._unpacked += len(xml)
            if self._unpacked > config.max_sitemap_files * config.max_sitemap_bytes:
                raise ValueError("shared sitemap decoded-byte accounting exceeded")
            if last.decoded_body.startswith(b"\x1f\x8b"):
                derived = self.runtime.store.write_evidence(
                    xml,
                    representation="sitemap_xml",
                    complete=complete,
                    source_evidence_id=ids[1],
                    transform_version="gzip-file-decoding-1",
                )
                ids = (*ids, derived.evidence_id)
            if not complete:
                state, truncated = "incomplete", True
            else:
                parsed = self.runtime.bounded_phase(
                    xml,
                    lambda data: parse_sitemap(
                        data,
                        config,
                        config.max_sitemap_urls - self._entry_slots,
                        self.runtime.check_time,
                    ),
                    max_bytes=config.max_sitemap_bytes,
                )
                state, reason, truncated = parsed.state, parsed.reason, parsed.truncated
                entries = tuple(parsed.entries)
                self._entry_slots += len(entries)
                accepted = tuple(e.url for e in entries if e.url is not None)
                if state != "invalid":
                    if parsed.kind == "urlset":
                        pages = accepted
                    else:
                        children = accepted
        self._truncated |= truncated
        return SitemapObservation.model_validate(
            dict(
                sitemap_id="sitemap-" + uuid.uuid4().hex,
                url=url,
                depth=depth,
                state=state,
                evidence_ids=ids,
                urls=pages,
                child_sitemaps=children,
                entries=entries,
                publisher_metadata=tuple(
                    (f"{e.ordinal}/{name}", value)
                    for e in entries
                    for name, value in e.publisher_metadata
                ),
                parsed_entries=len(entries),
                skipped_entries=None if truncated else sum(e.url is None for e in entries),
                reason_code=reason,
                truncated=truncated,
                sources=(source,),
            )
        )
