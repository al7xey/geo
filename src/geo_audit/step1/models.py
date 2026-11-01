"""Version-separated, evidence-backed contracts for static reading and step 2 handoff."""

from typing import Annotated, Literal, Self, cast

from pydantic import AfterValidator, Field, model_validator

from ..contracts import SHA256, Contract, Identifier, UTCDateTime
from ..errors import GeoAuditError
from ..models import URL, BudgetSnapshot, EvidenceFile
from ..request_policy import ResourcePurpose as ResourcePurpose
from ..urls import normalize_input
from .config import Step1Config

CheckStatus = Literal["pass", "fail", "warning", "unknown", "not_applicable"]
Decision = Literal["allowed", "disallowed", "unknown"]
Terminal = Literal[
    "queued",
    "fetched",
    "failed",
    "skipped_robots",
    "skipped_policy_unknown",
    "skipped_scope",
    "skipped_external",
    "skipped_excluded",
    "skipped_parameter_limit",
    "duplicate",
    "not_attempted_budget",
    "cancelled",
    "not_selected_limit",
]
Representation = Literal[
    "content_encoded",
    "entity_bytes",
    "decoded_text",
    "attempt_observation",
    "extracted_raw",
    "main_text",
    "rendered_dom",
    "user_material",
    "robots_text",
    "robots_decision",
    "sitemap_observation",
]
Role = Literal["entry", "about", "services", "prices", "contacts", "faq", "other"]


def _origin(value: str) -> str:
    try:
        actual = normalize_input(value).origin
    except GeoAuditError as error:
        raise ValueError("invalid origin") from error
    if actual != value:
        raise ValueError("expected exact scheme/host/port origin without path")
    return value


Origin = Annotated[str, AfterValidator(_origin)]


class Step1File(Contract):
    path: str
    sha256: SHA256
    size_bytes: int = Field(ge=0, le=64 * 1024 * 1024)
    complete: bool

    @model_validator(mode="after")
    def confined_path(self) -> Self:
        EvidenceFile(
            path=self.path,
            sha256=self.sha256,
            size_bytes=self.size_bytes,
            representation="attempt_observation",
            complete=self.complete,
        )
        return self


class Step1Header(Contract):
    name: Literal[
        "content-type",
        "content-length",
        "content-encoding",
        "location",
        "retry-after",
        "server",
        "date",
        "vary",
        "cache-control",
        "etag",
        "last-modified",
        "x-robots-tag",
    ]
    value: str
    redacted: bool = False


class SourceRef(Contract):
    run_id: Identifier
    site_sha256: SHA256
    manifest_sha256: SHA256
    source_step0_config_sha256: SHA256
    selected_evidence_id: Identifier


class Step1Evidence(Contract):
    evidence_id: Identifier
    run_id: Identifier
    file: Step1File
    representation: Representation
    source_evidence_id: Identifier | None
    source_run_id: Identifier | None
    transform_version: str | None
    original_evidence_id: Identifier | None

    @model_validator(mode="after")
    def derivation(self) -> Self:
        if self.source_evidence_id == self.evidence_id:
            raise ValueError("self-referential evidence")
        if self.source_evidence_id is not None and not self.transform_version:
            raise ValueError("derivation needs a version")
        if (self.source_run_id is None) != (self.original_evidence_id is None):
            raise ValueError("import needs both original run and evidence identity")
        if (
            self.representation
            in {
                "extracted_raw",
                "main_text",
                "rendered_dom",
                "robots_decision",
            }
            and self.source_evidence_id is None
        ):
            raise ValueError("derived representation requires its source")
        if not self.file.path.startswith("raw/") or len(self.file.path.split("/")) != 2:
            raise ValueError("evidence file must be directly under raw")
        return self


class TextRef(Contract):
    evidence_id: Identifier
    text_hash: SHA256
    text_chars: int = Field(ge=0)
    normalization_version: Literal["text-ws-1"] = "text-ws-1"


class QuoteRef(Contract):
    evidence_id: Identifier
    text_hash: SHA256
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.end <= self.start or self.end - self.start != len(self.text):
            raise ValueError("quote offsets must match nonempty Unicode text length")
        return self


class DiscoverySource(Contract):
    kind: Literal["entry", "html_link", "sitemap", "redirect"]
    source_id: Identifier
    occurrence: int = Field(ge=0)


class Candidate(Contract):
    candidate_id: Identifier
    url: URL
    sources: tuple[DiscoverySource, ...] = Field(min_length=1)
    role: Role
    role_basis: str
    language: str | None
    priority: int = Field(ge=0, le=6)
    ordinal: int = Field(ge=0)
    selected: bool
    state: Terminal
    reason_code: str | None
    alias_of: Identifier | None

    @model_validator(mode="after")
    def terminal(self) -> Self:
        if self.selected and self.state in {"queued", "not_selected_limit"}:
            raise ValueError("persisted selected candidates must be terminal")
        if not self.selected and self.state in {"fetched", "failed", "not_attempted_budget"}:
            raise ValueError("attempted candidate requires a selected slot")
        if self.alias_of == self.candidate_id:
            raise ValueError("self alias")
        if self.state == "duplicate" and self.alias_of is None:
            raise ValueError("duplicate requires alias identity")
        return self


class RobotsObservation(Contract):
    observation_id: Identifier
    origin: Origin
    final_url: URL | None
    state: Literal[
        "present",
        "empty",
        "absent",
        "access_denied",
        "rate_limited",
        "unreachable",
        "invalid_response",
        "incomplete",
    ]
    evidence_ids: tuple[Identifier, ...]
    status_code: int | None = Field(ge=100, le=599)
    observed_at: UTCDateTime
    parser_version: str
    warnings: tuple[str, ...]
    sitemap_urls: tuple[str, ...]
    truncated: bool


class RobotsDecision(Contract):
    decision_id: Identifier
    observation_id: Identifier
    evaluated_url: URL
    origin: Origin
    product_token: str = Field(min_length=1)
    decision: Decision
    robots_evidence_id: Identifier | None
    matched_group_lines: tuple[int, ...]
    decisive_rule_lines: tuple[int, ...]
    reason_code: str
    parser_version: str
    crawl_delay: float | None = Field(ge=0)

    @model_validator(mode="after")
    def rule_evidence(self) -> Self:
        if any(n < 1 for n in (*self.matched_group_lines, *self.decisive_rule_lines)):
            raise ValueError("line numbers are one-based")
        if (self.matched_group_lines or self.decisive_rule_lines) and not self.robots_evidence_id:
            raise ValueError("rule lines require evidence")
        if self.decision == "disallowed" and not self.decisive_rule_lines:
            raise ValueError("prohibition requires an actual rule")
        return self


class SitemapObservation(Contract):
    sitemap_id: Identifier
    url: URL
    depth: int = Field(ge=1, le=3)
    state: Literal["parsed", "absent", "invalid", "incomplete", "skipped", "failed"]
    evidence_ids: tuple[Identifier, ...]
    urls: tuple[URL, ...]
    child_sitemaps: tuple[URL, ...]
    publisher_metadata: tuple[tuple[str, str], ...]
    parsed_entries: int = Field(ge=0)
    skipped_entries: int | None = Field(ge=0)
    reason_code: str | None
    truncated: bool


class DirectiveObservation(Contract):
    source: Literal["http_header", "html_meta", "data_nosnippet", "canonical"]
    representation: Literal["static", "rendered"]
    evidence_id: Identifier
    recipient: str | None
    raw: str
    tokens: tuple[str, ...]


class Signal(Contract):
    kind: Literal["lang", "hreflang", "address", "service_geography", "tld", "phone"]
    value: str
    strength: Literal["explicit", "weak"]
    quote: QuoteRef | None
    evidence_id: Identifier


class Page(Contract):
    page_id: Identifier
    candidate_id: Identifier
    url: URL
    effective_url: URL
    evidence_id: Identifier
    title: str | None
    headings: tuple[str, ...]
    lists: tuple[str, ...]
    tables: tuple[str, ...]
    main_text: TextRef | None
    alternate_texts: tuple[TextRef, ...]
    extraction_method: str | None
    extractor_version: str | None
    extraction_reason: str
    content_state: Literal[
        "html",
        "js_candidate",
        "access_barrier",
        "suspicious_html",
        "empty",
        "non_html",
        "incomplete",
        "too_large",
        "soft_404",
        "service_content",
    ]
    useful_candidate: bool
    supporting_page: bool
    business_relevance: Literal["observed", "not_observed", "unknown"]
    business_quotes: tuple[QuoteRef, ...]
    directives: tuple[DirectiveObservation, ...]
    signals: tuple[Signal, ...]
    header_capture_complete: bool
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def text_state(self) -> Self:
        if (self.useful_candidate or self.supporting_page) and self.main_text is None:
            raise ValueError("material requires a text representation")
        if self.business_relevance == "observed" and not self.business_quotes:
            raise ValueError("business observation requires quoted evidence")
        if self.business_quotes and self.main_text is None:
            raise ValueError("business quotes require the page text")
        if self.useful_candidate and self.supporting_page:
            raise ValueError("useful and supporting are disjoint")
        return self


class TechnicalCheck(Contract):
    check_id: Identifier
    kind: Literal[
        "robots_policy",
        "indexing_directives",
        "snippet_directives",
        "canonical_signal",
        "observed_access",
        "https_transport",
        "sitemap",
        "javascript",
        "ua_comparison",
    ]
    url: URL | None
    page_id: Identifier | None
    robot_id: str | None
    status: CheckStatus
    observation: str
    evidence_ids: tuple[Identifier, ...]
    decisive_rule_lines: tuple[int, ...]
    reason_code: str
    limitation: str
    observed_at: UTCDateTime
    rule_version: str
    scope: str

    @model_validator(mode="after")
    def basis(self) -> Self:
        if self.status in {"pass", "fail", "warning"} and not self.evidence_ids:
            raise ValueError("observed check needs evidence")
        if self.reason_code == "CONFIG_DISABLED" and self.status != "not_applicable":
            raise ValueError("disabled is not an unknown or failure")
        return self


class DiagnosticResult(Contract):
    diagnostic_id: Identifier
    kind: Literal["browser", "ua_probe", "https_alternative"]
    url: URL | None
    status: CheckStatus
    reason_code: str
    evidence_ids: tuple[Identifier, ...]
    selection_reason: str
    outcome: Literal["observed_gain", "no_material_gain", "inconclusive"] | None
    limitation: str


class UserMaterial(Contract):
    material_id: Identifier
    source_type: Literal["user_provided"] = "user_provided"
    author_type: Literal["user"] = "user"
    provided_at: UTCDateTime
    text: TextRef
    suitability: Literal["usable", "needs_clarification", "unknown"]
    reason: str


class Coverage(Contract):
    discovered_urls: int = Field(ge=0)
    selected_candidates: int = Field(ge=0)
    fetch_successes: int = Field(ge=0)
    unique_html_pages: int = Field(ge=0)
    extracted_pages: int = Field(ge=0)
    useful_pages: int = Field(ge=0)
    skipped: int = Field(ge=0)
    failed: int = Field(ge=0)
    discarded_entries: int = Field(ge=0)
    skip_examples: tuple[str, ...]
    skip_examples_truncated: bool
    sitemap_truncated: bool


class Material(Contract):
    state: Literal["usable", "limited", "insufficient", "needs_description", "user_supported"]
    can_start_step2: bool
    reason: str
    useful_page_ids: tuple[Identifier, ...]
    supporting_page_ids: tuple[Identifier, ...]
    user_materials: tuple[UserMaterial, ...]

    @model_validator(mode="after")
    def gate(self) -> Self:
        if self.can_start_step2 != (self.state in {"usable", "limited", "user_supported"}):
            raise ValueError("material gate differs from state")
        if set(self.useful_page_ids) & set(self.supporting_page_ids):
            raise ValueError("material page roles overlap")
        unique(self.useful_page_ids, "useful page identity")
        unique(self.supporting_page_ids, "supporting page identity")
        unique(tuple(u.material_id for u in self.user_materials), "user material identity")
        if self.state == "user_supported" and not any(
            u.suitability == "usable" for u in self.user_materials
        ):
            raise ValueError("user-supported gate needs suitable user material")
        if self.state in {"usable", "limited"} and not self.useful_page_ids:
            raise ValueError("site material requires useful pages")
        return self


class Envelope(Contract):
    schema_version: Literal["step1-1.0.0"] = "step1-1.0.0"
    algorithm_version: Literal["step1-1.0"] = "step1-1.0"
    run_id: Identifier
    publication_id: Identifier
    config_hash: SHA256
    registry_version: str = Field(min_length=1)
    started_at: UTCDateTime
    finished_at: UTCDateTime

    @model_validator(mode="after")
    def chronology(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finish predates start")
        return self


def unique(values: tuple[str, ...], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError("duplicate " + label)


class CrawlResult(Envelope):
    candidates: tuple[Candidate, ...]
    pages: tuple[Page, ...]
    robots: tuple[RobotsObservation, ...]
    robots_decisions: tuple[RobotsDecision, ...]
    sitemaps: tuple[SitemapObservation, ...]
    pending_external_urls: tuple[URL, ...]
    coverage: Coverage
    material: Material

    @model_validator(mode="after")
    def coverage_and_ids(self) -> Self:
        unique(tuple(c.candidate_id for c in self.candidates), "candidate ID")
        unique(tuple(c.url for c in self.candidates), "candidate URL")
        unique(tuple(p.page_id for p in self.pages), "page ID")
        unique(tuple(p.candidate_id for p in self.pages), "page candidate")
        candidates = {c.candidate_id: c for c in self.candidates}
        for p in self.pages:
            if p.candidate_id not in candidates or candidates[p.candidate_id].state != "fetched":
                raise ValueError("page requires a fetched candidate")
        for c in self.candidates:
            if c.alias_of is not None and c.alias_of not in candidates:
                raise ValueError("dangling candidate alias")
            if c.state == "queued":
                raise ValueError("final crawl cannot contain unexplained queued candidates")
            visited = {c.candidate_id}
            current = c
            while current.alias_of is not None:
                if current.alias_of not in candidates:
                    raise ValueError("dangling candidate alias in chain")
                if current.alias_of in visited:
                    raise ValueError("cyclic candidate aliases")
                visited.add(current.alias_of)
                current = candidates[current.alias_of]
        page_ids = {p.page_id for p in self.pages}
        if (
            not set((*self.material.useful_page_ids, *self.material.supporting_page_ids))
            <= page_ids
        ):
            raise ValueError("dangling material page")
        if set(self.material.useful_page_ids) != {
            p.page_id for p in self.pages if p.useful_candidate
        }:
            raise ValueError("material differs from useful pages")
        if set(self.material.supporting_page_ids) != {
            p.page_id for p in self.pages if p.supporting_page
        }:
            raise ValueError("material differs from supporting pages")
        business_pages = [
            p for p in self.pages if p.useful_candidate and p.business_relevance == "observed"
        ]
        if self.material.state in {"usable", "limited"} and not business_pages:
            raise ValueError("site material gate needs business evidence")
        if self.material.state == "usable" and len(business_pages) < 3:
            raise ValueError("usable requires three useful business pages")
        if self.material.state == "limited" and len(business_pages) > 2:
            raise ValueError("three useful business pages are usable, not limited")
        counts = {
            "discovered_urls": len(self.candidates),
            "selected_candidates": sum(c.selected for c in self.candidates),
            "fetch_successes": sum(c.state == "fetched" for c in self.candidates),
            "unique_html_pages": len({p.effective_url for p in self.pages}),
            "extracted_pages": sum(p.main_text is not None for p in self.pages),
            "useful_pages": sum(p.useful_candidate for p in self.pages),
            "skipped": sum(c.state.startswith("skipped_") for c in self.candidates),
            "failed": sum(c.state == "failed" for c in self.candidates),
        }
        if any(getattr(self.coverage, k) != v for k, v in counts.items()):
            raise ValueError("coverage differs from terminal observations")
        return self


class ChecksResult(Envelope):
    checks: tuple[TechnicalCheck, ...]
    diagnostics: tuple[DiagnosticResult, ...]


class RunResult(Envelope):
    status: Literal["ok", "partial", "needs_input", "failed"]
    lifecycle: Literal["completed", "cancelled"]
    stop_reason: str
    budget: BudgetSnapshot
    browser_attempts: int = Field(ge=0, le=200)
    cache_hits: int = Field(ge=0)
    unfinished_urls: tuple[URL, ...]
    limitations: tuple[str, ...]


class Step1Result(Contract):
    schema_version: Literal["step1-1.0.0"] = "step1-1.0.0"
    source: SourceRef
    config: Step1Config
    evidence: tuple[Step1Evidence, ...]
    crawl: CrawlResult
    checks: ChecksResult
    run: RunResult

    @model_validator(mode="after")
    def consistency(self) -> Self:
        if self.source.source_step0_config_sha256 != self.config.source_step0_config_sha256:
            raise ValueError("source config hash mismatch")
        for artifact in (self.crawl, self.checks, self.run):
            if (
                artifact.run_id != self.source.run_id
                or artifact.config_hash != self.config.fingerprint()
            ):
                raise ValueError("run/config identity mismatch")
            if artifact.model_dump(include=set(Envelope.model_fields)) != self.run.model_dump(
                include=set(Envelope.model_fields)
            ):
                raise ValueError("artifact envelope mismatch")
        unique(tuple(e.evidence_id for e in self.evidence), "evidence ID")
        unique(tuple(c.check_id for c in self.checks.checks), "check ID")
        unique(tuple(d.diagnostic_id for d in self.checks.diagnostics), "diagnostic ID")
        unique(
            tuple(e.evidence_id for e in self.evidence)
            + tuple(c.candidate_id for c in self.crawl.candidates)
            + tuple(p.page_id for p in self.crawl.pages)
            + tuple(c.check_id for c in self.checks.checks)
            + tuple(d.diagnostic_id for d in self.checks.diagnostics)
            + tuple(r.observation_id for r in self.crawl.robots)
            + tuple(r.decision_id for r in self.crawl.robots_decisions)
            + tuple(s.sitemap_id for s in self.crawl.sitemaps)
            + tuple(u.material_id for u in self.crawl.material.user_materials),
            "cross-entity identity",
        )
        evidence = {e.evidence_id: e for e in self.evidence}
        pages = {p.page_id: p for p in self.crawl.pages}
        known_sources = set(evidence) | set(pages) | {s.sitemap_id for s in self.crawl.sitemaps}
        for candidate in self.crawl.candidates:
            if any(s.source_id not in known_sources for s in candidate.sources):
                raise ValueError("dangling discovery source")
        if not any(
            e.evidence_id == self.source.selected_evidence_id
            or e.original_evidence_id == self.source.selected_evidence_id
            for e in self.evidence
        ):
            raise ValueError("selected source evidence missing")
        unique(tuple(e.file.path for e in self.evidence), "evidence path")
        if sum(e.file.size_bytes for e in self.evidence) > 256 * 1024 * 1024:
            raise ValueError("evidence budget exceeded")

        def ref(identity: str) -> Step1Evidence:
            if identity not in evidence:
                raise ValueError("dangling evidence " + identity)
            return evidence[identity]

        texts: dict[str, TextRef] = {}

        def text_ref(value: TextRef) -> None:
            ev = ref(value.evidence_id)
            if ev.file.sha256 != value.text_hash or not ev.file.complete:
                raise ValueError("text evidence mismatch/incomplete")
            if ev.representation not in {"main_text", "extracted_raw", "user_material"}:
                raise ValueError("text refers to non-text evidence")
            if value.text_chars > ev.file.size_bytes:
                raise ValueError("text character count exceeds UTF-8 byte size")
            if value.evidence_id in texts and texts[value.evidence_id] != value:
                raise ValueError("conflicting text reference")
            texts[value.evidence_id] = value

        def quote(value: QuoteRef) -> None:
            ev = ref(value.evidence_id)
            text = texts.get(value.evidence_id)
            if (
                text is None
                or not ev.file.complete
                or ev.file.sha256 != value.text_hash
                or value.end > text.text_chars
            ):
                raise ValueError("quote evidence/hash/bounds mismatch")

        for ev in self.evidence:
            if ev.run_id != self.source.run_id:
                raise ValueError("foreign run evidence")
            if ev.source_run_id is not None and ev.source_run_id != self.source.run_id:
                raise ValueError("foreign source run import")
            if ev.source_evidence_id is not None:
                ref(ev.source_evidence_id)
            seen = {ev.evidence_id}
            current = ev
            while current.source_evidence_id is not None:
                identity = current.source_evidence_id
                if identity in seen:
                    raise ValueError("cyclic derivation")
                seen.add(identity)
                current = ref(identity)
        for page in self.crawl.pages:
            if page.main_text:
                text_ref(page.main_text)
            for t in page.alternate_texts:
                text_ref(t)
        for user in self.crawl.material.user_materials:
            text_ref(user.text)
            if ref(user.text.evidence_id).representation != "user_material":
                raise ValueError("user material needs its own evidence representation")
        for page in self.crawl.pages:
            body = ref(page.evidence_id)
            for text in ((page.main_text,) if page.main_text else ()) + page.alternate_texts:
                ancestor = ref(text.evidence_id)
                while ancestor.source_evidence_id is not None:
                    ancestor = ref(ancestor.source_evidence_id)
                    if ancestor.evidence_id == page.evidence_id:
                        break
                else:
                    raise ValueError("text derivation does not originate from this page")
            if page.main_text and ref(page.main_text.evidence_id).representation != "main_text":
                raise ValueError("main page text needs main_text representation")
            if page.useful_candidate and (
                page.main_text is None or page.main_text.text_chars < self.config.useful_chars
            ):
                raise ValueError("useful page text is below configured threshold")
            if (page.useful_candidate or page.supporting_page) and not body.file.complete:
                raise ValueError("useful/supporting page requires complete source")
            for q in page.business_quotes:
                quote(q)
                if page.main_text is None or (q.text_hash, q.evidence_id) != (
                    page.main_text.text_hash,
                    page.main_text.evidence_id,
                ):
                    raise ValueError("business quote differs from page text")
                if q.end > page.main_text.text_chars:
                    raise ValueError("quote exceeds page character count")
            for d in page.directives:
                ref(d.evidence_id)
            for signal in page.signals:
                ref(signal.evidence_id)
                if signal.quote:
                    quote(signal.quote)
        for observation in cast(
            tuple[RobotsObservation | SitemapObservation, ...],
            (*self.crawl.robots, *self.crawl.sitemaps),
        ):
            for identity in observation.evidence_ids:
                ref(identity)
        for decision in self.crawl.robots_decisions:
            if decision.observation_id not in {r.observation_id for r in self.crawl.robots}:
                raise ValueError("dangling robots observation")
            if decision.robots_evidence_id:
                ref(decision.robots_evidence_id)
            observation = next(
                r for r in self.crawl.robots if r.observation_id == decision.observation_id
            )
            if decision.origin != observation.origin:
                raise ValueError("decision differs from original robots origin")
            if normalize_input(decision.evaluated_url).origin != decision.origin:
                raise ValueError("robots decision URL differs from evaluated origin")
            if decision.robots_evidence_id and (
                decision.robots_evidence_id not in observation.evidence_ids
            ):
                raise ValueError("decision cites another robots file")
        for check in self.checks.checks:
            if check.page_id is not None and check.page_id not in pages:
                raise ValueError("dangling check page")
        for item in cast(
            tuple[TechnicalCheck | DiagnosticResult, ...],
            (*self.checks.checks, *self.checks.diagnostics),
        ):
            for identity in item.evidence_ids:
                ref(identity)
        if len(self.crawl.candidates) > self.config.max_candidates:
            raise ValueError("queue cap exceeded")
        if self.crawl.coverage.selected_candidates > self.config.max_selected:
            raise ValueError("selection cap exceeded")
        if len(self.crawl.sitemaps) > self.config.max_sitemap_files:
            raise ValueError("sitemap file cap exceeded")
        if sum(s.parsed_entries for s in self.crawl.sitemaps) > self.config.max_sitemap_urls:
            raise ValueError("sitemap entry cap exceeded")
        if any(s.depth > self.config.max_sitemap_depth for s in self.crawl.sitemaps):
            raise ValueError("sitemap depth cap exceeded")
        if self.run.browser_attempts > self.config.max_browser_attempts:
            raise ValueError("browser attempt cap exceeded")
        unique(tuple(r.observation_id for r in self.crawl.robots), "robots observation")
        unique(tuple(r.decision_id for r in self.crawl.robots_decisions), "robots decision")
        if self.run.status == "ok" and not self.crawl.material.can_start_step2:
            raise ValueError("ok run requires suitable material")
        return self


class Step2Input(Contract):
    schema_version: Literal["step2-input-1.0.0"] = "step2-input-1.0.0"
    source_step: Literal[1] = 1
    source_commit_sha256: SHA256
    result: Step1Result
    useful_texts: tuple[TextRef, ...]
    supporting_texts: tuple[TextRef, ...]

    @model_validator(mode="after")
    def corpus(self) -> Self:
        material = self.result.crawl.material
        if not material.can_start_step2:
            raise ValueError("material does not permit step 2")
        pages = {p.page_id: p for p in self.result.crawl.pages}
        expected = tuple(pages[i].main_text for i in material.useful_page_ids)
        supporting = tuple(pages[i].main_text for i in material.supporting_page_ids)
        if expected != self.useful_texts or supporting != self.supporting_texts:
            raise ValueError("corpus differs from selected material")
        if self.result.run.status == "failed":
            raise ValueError("failed run cannot produce ready input")
        return self
