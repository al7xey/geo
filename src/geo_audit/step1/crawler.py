"""Sequential deterministic selection; quotas belong to the existing runtime, never a new budget."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from ..content import assess_response
from ..errors import GeoAuditError
from ..request_policy import RequestAuthorization
from ..scope import scope_reason
from ..urls import normalize_input, resolve_location
from .discovery import EntryLinks
from .models import Candidate, Coverage, DiscoverySource, Page, Role, TechnicalCheck
from .storage import encoded_json

if TYPE_CHECKING:
    from .runner import Step1Runtime

ROLE_SEGMENTS: tuple[tuple[Role, frozenset[str]], ...] = (
    ("about", frozenset({"about", "about-us", "company", "o-kompanii", "о-компании"})),
    ("services", frozenset({"services", "products", "solutions", "uslugi", "catalog", "услуги"})),
    ("prices", frozenset({"prices", "pricing", "price", "tariffs", "цены"})),
    ("contacts", frozenset({"contacts", "contact", "locations", "geography", "контакты"})),
    ("faq", frozenset({"faq", "questions", "help"})),
)
TRAP_PARAMETERS = frozenset({"q", "query", "search", "logout", "add-to-cart", "calendar", "sid"})
# Account flows carry no business text; fixed here so the config fingerprint stays stable.
ACCOUNT_SEGMENTS = frozenset(
    {"signin", "signup", "register", "registration", "recovery", "password", "auth"}
)
# Navigation/analytics markers: the same page under another query, never new content.
TRACKING_PARAMETERS = frozenset(
    {"opened_from", "from", "source", "ref", "referrer", "gclid", "yclid", "fbclid", "_openstat"}
)
NON_HTML_SUFFIXES = (
    ".pdf", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".avif",
    ".zip", ".rar", ".7z", ".gz", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".mp3", ".mp4", ".avi", ".mov", ".webm", ".css", ".js", ".json", ".xml", ".txt",
)  # fmt: skip


def strip_tracking(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMETERS and not k.lower().startswith("utm_")
    ]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def role_for(url: str, *, entry: bool = False) -> tuple[Role, int, str]:
    segments = set(unquote(urlsplit(url).path).lower().strip("/").split("/"))
    if entry or not segments - {"", "index.html", "index.htm"}:
        return "entry", 0, "entry URL or exact root/index path; role-url-1 heuristic"
    for priority, (role, tokens) in enumerate(ROLE_SEGMENTS, 1):
        match = sorted(segments & tokens)
        if match:
            return role, priority, "role-url-1 exact path segment: " + match[0]
    return "other", 6, "role-url-1: no recognized path segment; not semantic classification"


class Crawler:
    def __init__(self, runtime: Step1Runtime):
        self.runtime = runtime
        self.candidates: dict[str, Candidate] = {}
        self.pages: list[Page] = []
        self.external: dict[str, None] = {}
        self.variants: dict[tuple[str, str], set[str]] = {}
        self.effective: dict[str, str] = {}
        self.discarded = 0
        self.examples: list[str] = []
        self.truncated = False
        self.started = False
        self.completed = False
        self.checks: list[TechnicalCheck] = []
        self.comparisons: dict[str, tuple[frozenset[str], int]] = {}
        self.conditions: dict[str, tuple[int, tuple[str, ...]]] = {}

    def exclusion(self, url: str) -> str | None:
        parts = urlsplit(url)
        segments = {s.lower() for s in unquote(parts.path).split("/")}
        if segments & (set(self.runtime.config.excluded_path_segments) | ACCOUNT_SEGMENTS):
            return "PATH_EXCLUDED"
        if unquote(parts.path).lower().endswith(NON_HTML_SUFFIXES):
            return "PATH_EXCLUDED"
        parameters = parse_qsl(parts.query)
        if any(key.lower() in TRAP_PARAMETERS for key, _ in parameters) or any(
            key.lower() in {"action", "do", "cmd"}
            and value.lower() in self.runtime.config.excluded_path_segments
            for key, value in parameters
        ):
            return "PARAMETER_TRAP"
        return None

    async def authorize(self, url: str) -> RequestAuthorization:
        reason = self.exclusion(url)
        if reason:
            return RequestAuthorization("disallowed", reason)
        return await self.runtime.robots.authorize(url)

    def discard(self, url: str, reason: str) -> None:
        self.discarded += 1
        if len(self.examples) < self.runtime.config.max_skip_examples:
            self.examples.append(reason + ": " + url[:2048])
        else:
            self.truncated = True

    def admit(self, raw: str, source: DiscoverySource, *, entry: bool = False) -> None:
        try:
            url = normalize_input(raw if entry else strip_tracking(raw))
        except GeoAuditError:
            self.discard(raw, "URL_INVALID")
            return
        if url.url in self.candidates:
            current = self.candidates[url.url]
            if source not in current.sources:
                self.candidates[url.url] = current.model_copy(
                    update={"sources": (*current.sources, source)}
                )
            return
        if len(self.candidates) >= self.runtime.config.max_candidates:
            self.discard(url.url, "CANDIDATE_LIMIT")
            return
        role, priority, basis = role_for(url.url, entry=entry)
        state, reason = "queued", None
        if scope_reason(url, self.runtime.scope):
            state, reason = "skipped_external", "OUTSIDE_HTML_SCOPE"
            if len(self.external) < self.runtime.config.max_candidates:
                self.external[url.url] = None
        elif not entry and (reason := self.exclusion(url.url)):
            state = "skipped_excluded"
        else:
            parts = urlsplit(url.url)
            variants = self.variants.setdefault((url.origin, parts.path), set())
            if (
                parts.query not in variants
                and len(variants) >= self.runtime.config.max_query_variants
            ):
                state, reason = "skipped_parameter_limit", "QUERY_VARIANT_LIMIT"
            else:
                variants.add(parts.query)
        self.candidates[url.url] = Candidate.model_validate(
            dict(
                candidate_id=f"candidate-{len(self.candidates) + 1:05}",
                url=url.url,
                sources=(source,),
                role=role,
                role_basis=basis,
                language=None,
                priority=priority,
                ordinal=len(self.candidates),
                selected=entry,
                state="fetched" if entry else state,
                reason_code="REUSED_EVIDENCE" if entry else reason,
                alias_of=None,
            )
        )

    def links(self, html: str, url: str, page_id: str) -> None:
        scanner = EntryLinks(self.runtime.config.max_candidates)
        try:
            scanner.feed(html)
            scanner.close()
        except AssertionError as error:
            if not str(error).startswith(("unknown status keyword ", "expected name token at ")):
                raise
            self.discard(url, "HTML_LINK_PARSE_INCOMPLETE")
        base = url
        if scanner.base:
            try:
                base = resolve_location(url, scanner.base).url
            except GeoAuditError:
                self.discard(scanner.base, "INVALID_BASE")
        for href, occurrence in scanner.links.items():
            self.runtime.check_time()
            try:
                target = resolve_location(base, href).url
            except GeoAuditError:
                self.discard(href, "INVALID_LINK")
                continue
            self.admit(
                target, DiscoverySource(kind="html_link", source_id=page_id, occurrence=occurrence)
            )
        if scanner.truncated:
            self.truncated = True

    def page(
        self,
        candidate: Candidate,
        effective_url: str,
        evidence_id: str,
        content_state: str,
        *,
        complete_headers: bool,
    ) -> Page:
        return Page.model_validate(
            dict(
                page_id=f"page-{len(self.pages) + 1:05}",
                candidate_id=candidate.candidate_id,
                url=candidate.url,
                effective_url=effective_url,
                evidence_id=evidence_id,
                title=None,
                headings=(),
                lists=(),
                tables=(),
                main_text=None,
                alternate_texts=(),
                extraction_method=None,
                extractor_version=None,
                extraction_reason="EXTRACTION_NOT_IMPLEMENTED",
                content_state=content_state,
                useful_candidate=False,
                supporting_page=False,
                business_relevance="unknown",
                business_quotes=(),
                directives=(),
                signals=(),
                header_capture_complete=complete_headers,
                limitations=("EXTRACTION_NOT_IMPLEMENTED",),
            )
        )

    async def run(self) -> None:
        if self.started:
            raise ValueError("crawler cannot restart its selection quotas")
        self.started = True
        runtime = self.runtime
        url = runtime.handoff.contract.site.effective_url
        if url is None:
            raise ValueError("verified runtime lost its entry URL")
        self.admit(
            url,
            DiscoverySource(kind="entry", source_id=runtime.store.entry_evidence_id, occurrence=0),
            entry=True,
        )
        self.pages.append(
            self.page(
                self.candidates[url],
                url,
                runtime.store.entry_evidence_id,
                runtime.handoff.contract.site.content_state,
                complete_headers=runtime.entry_headers_complete,
            )
        )
        self.extract(
            -1,
            runtime.entry.html_text,
            runtime.entry_headers,
            runtime.entry.response.status_code or 200,
            runtime.entry.response.tls_state,
            runtime.entry_header_evidence_id,
        )
        self.effective[url] = self.candidates[url].candidate_id
        await runtime.robots.evaluate_all(url)
        discovered = await runtime.discover_urls()
        for item in discovered.urls:
            for source in item.sources:
                self.admit(item.url, source)
        # Entry external links are retained separately by discovery; no scope permission is added.
        self.external.update(dict.fromkeys(runtime.discovery.external_urls))
        while True:
            pending = [c for c in self.candidates.values() if c.state == "queued"]
            if not pending:
                break
            if sum(c.selected for c in self.candidates.values()) >= runtime.config.max_selected:
                break
            runtime.check_time()
            current = min(pending, key=lambda c: (c.priority, c.ordinal, c.url))
            if current.url in self.effective:
                self.candidates[current.url] = current.model_copy(
                    update={
                        "state": "duplicate",
                        "alias_of": self.effective[current.url],
                        "reason_code": "OBSERVED_REDIRECT_ALIAS",
                    }
                )
                continue
            # A selected slot is terminal even if cancellation/programming error interrupts fetch.
            current = current.model_copy(
                update={"selected": True, "state": "cancelled", "reason_code": "INTERRUPTED"}
            )
            self.candidates[current.url] = current
            trace = await runtime.fetch_html(current.url)
            last = trace.attempts[-1] if trace.attempts else None
            state = "failed"
            reason: str = trace.error.code if trace.error else "HTTP_OR_CONTENT_ERROR"
            if trace.policy_denial:
                reason = trace.policy_denial.reason_code
                state = (
                    "skipped_policy_unknown"
                    if trace.policy_denial.decision == "unknown"
                    else "skipped_excluded"
                    if reason in {"PATH_EXCLUDED", "PARAMETER_TRAP"}
                    else "skipped_robots"
                )
                if reason == "HTML_CACHE_ALIAS":
                    target = trace.redirects[-1].target_url
                    if target is None:
                        raise ValueError("cached redirect lost its target")
                    current = current.model_copy(update={"alias_of": self.effective[target]})
                    state, reason = "duplicate", "REUSED_REDIRECT_TARGET"
                    runtime.note_cache_hit()
            elif trace.error and trace.error.code == "REDIRECT_EXTERNAL_HOST":
                state = "skipped_external"
            elif trace.error and trace.error.code.startswith(("BUDGET_", "RETRY_")):
                state = "not_attempted_budget"
                runtime._stop_reason = trace.error.code
            elif not trace.error and last and last.status_code and 200 <= last.status_code < 300:
                assessment = assess_response(last)
                if assessment.text is not None and last.body_complete and last.status_code != 206:
                    state, reason = "fetched", "HTML_OBSERVED"
                    alias = self.effective.get(last.requested_url)
                    if alias:
                        current = current.model_copy(update={"alias_of": alias})
                    else:
                        self.effective[last.requested_url] = current.candidate_id
                    for hop in trace.redirects:
                        if hop.target_url and hop.decision == "needs_input":
                            self.external[hop.target_url] = None
                    entity = runtime.store.attempt_evidence[last.attempt_id][1]
                    runtime.store.write_evidence(
                        encoded_json(
                            dict(
                                applied_encoding=assessment.applied_encoding,
                                declared_encoding=assessment.declared_encoding,
                                replacement_characters=assessment.replacements,
                                sniffed=assessment.sniffed,
                                classification_basis=assessment.basis,
                                classification_confidence=assessment.confidence,
                            )
                        ),
                        representation="attempt_observation",
                        complete=True,
                        source_evidence_id=entity,
                        transform_version="html-assessment-step0-1",
                    )
                    page = self.page(
                        current, last.requested_url, entity, assessment.state, complete_headers=True
                    )
                    self.pages.append(page)
                    self.candidates[current.url] = current.model_copy(
                        update={"state": "fetched", "reason_code": reason}
                    )
                    if not alias:
                        self.extract(
                            -1,
                            assessment.text,
                            last.resource_headers,
                            last.status_code,
                            last.tls_state,
                            runtime.store.attempt_evidence[last.attempt_id][2],
                        )
                        self.links(assessment.text, last.requested_url, page.page_id)
            for hop in trace.redirects:
                if hop.target_url and hop.decision == "needs_input":
                    if len(self.external) < runtime.config.max_candidates:
                        self.external[hop.target_url] = None
            self.candidates[current.url] = self.candidates[current.url].model_copy(
                update={"state": state, "reason_code": reason, "alias_of": current.alias_of}
            )
            if state == "not_attempted_budget":
                break
        self.completed = True

    def extract(
        self,
        index: int,
        html: str,
        headers: tuple[tuple[str, str], ...],
        status: int,
        tls: str,
        header_evidence: str | None = None,
    ) -> None:
        from .checks import page_checks
        from .diagnostics import visible_words
        from .extraction import extract_page

        page = extract_page(self.runtime, self.pages[index], html, headers, header_evidence)
        duplicate = next(
            (
                p
                for p in self.pages
                if p.page_id != page.page_id
                and p.main_text
                and page.main_text
                and p.main_text.text_hash == page.main_text.text_hash
            ),
            None,
        )
        if duplicate:
            page = page.model_copy(
                update={
                    "limitations": (
                        *page.limitations,
                        "DUPLICATE_TEXT_CANDIDATE:" + duplicate.page_id,
                    )
                }
            )
        self.pages[index] = page
        if self.runtime.config.ua_probes_enabled:
            self.comparisons[page.page_id] = visible_words(html)
            self.conditions[page.page_id] = (
                status,
                tuple(
                    v.partition(";")[0].strip().lower()
                    for k, v in headers
                    if k.lower() == "content-type"
                ),
            )
        self.checks.extend(page_checks(self.runtime, page, status, tls, header_evidence))

    def finalize(self, *, cancelled: bool) -> Coverage:
        for url, candidate in self.candidates.items():
            if candidate.state == "queued":
                self.candidates[url] = candidate.model_copy(
                    update={
                        "state": "cancelled" if cancelled else "not_selected_limit",
                        "reason_code": "CANCELLED" if cancelled else "SELECTION_OR_BUDGET_LIMIT",
                    }
                )
        candidates = list(self.candidates.values())
        return Coverage(
            discovered_urls=len(candidates),
            selected_candidates=sum(c.selected for c in candidates),
            fetch_successes=sum(c.state == "fetched" for c in candidates),
            unique_html_pages=len({p.effective_url for p in self.pages}),
            extracted_pages=sum(p.main_text is not None for p in self.pages),
            useful_pages=sum(p.useful_candidate for p in self.pages),
            skipped=sum(c.state.startswith("skipped_") for c in candidates),
            failed=sum(c.state == "failed" for c in candidates),
            discarded_entries=self.discarded,
            skip_examples=tuple(self.examples),
            skip_examples_truncated=self.truncated,
            sitemap_truncated=self.runtime.discovery.sitemap_truncated,
        )
