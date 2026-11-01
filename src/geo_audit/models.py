"""Persisted contracts. Required nullable fields distinguish unknown from absent keys."""

import re
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, Field, PrivateAttr, field_validator, model_validator

from .config import Step0Config
from .connection import is_public_address
from .contracts import (
    SHA256,
    AlgorithmVersion,
    ConfigVersion,
    Contract,
    Identifier,
    NonNegativeInt,
    SchemaVersion,
    UTCDateTime,
)
from .errors import ErrorCode, ErrorDetail
from .urls import canonical_url, normalize_host, normalize_input

URL = Annotated[
    str,
    Field(pattern=r"^https?://", json_schema_extra={"format": "uri"}),
    AfterValidator(canonical_url),
]
ContentState = Literal[
    "not_received",
    "html",
    "js_candidate",
    "access_barrier",
    "suspicious_html",
    "empty",
    "non_html",
    "http_denied",
    "http_missing",
    "http_unavailable",
    "http_other",
    "incomplete",
    "too_large",
]
Status = Literal["ok", "partial", "needs_input", "failed"]
TLSState = Literal[
    "not_observed", "not_applicable", "verified", "certificate_error", "handshake_error"
]
USABLE_STATES = {"html", "js_candidate", "suspicious_html"}


class BudgetSnapshot(Contract):
    schema_version: SchemaVersion
    started_at: UTCDateTime
    captured_at: UTCDateTime
    step0_deadline_at: UTCDateTime
    total_deadline_at: UTCDateTime
    elapsed_seconds: float = Field(ge=0)
    step0_attempts: NonNegativeInt
    total_attempts: NonNegativeInt
    step0_max_attempts: int = Field(ge=1)
    total_max_attempts: int = Field(ge=1)

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if not self.started_at <= self.step0_deadline_at <= self.total_deadline_at:
            raise ValueError("invalid budget deadlines")
        if self.captured_at < self.started_at:
            raise ValueError("capture predates run")
        if (
            abs((self.captured_at - self.started_at).total_seconds() - self.elapsed_seconds)
            > 0.000001
        ):
            raise ValueError("elapsed time differs from captured UTC interval")
        if not 0 <= self.step0_attempts <= self.total_attempts <= self.total_max_attempts:
            raise ValueError("inconsistent request counters")
        if self.step0_attempts > self.step0_max_attempts:
            raise ValueError("step 0 request limit exceeded")
        if self.step0_max_attempts > self.total_max_attempts:
            raise ValueError("step 0 limit exceeds shared limit")
        return self


class ResolvedTarget(Contract):
    host: str
    port: int = Field(ge=1, le=65535)
    addresses: tuple[str, ...] = Field(min_length=1)
    connected_ip: str | None
    enforcement: Literal["pinned", "controlled_egress"]

    @model_validator(mode="after")
    def public_addresses(self) -> Self:
        if normalize_host(self.host) != self.host:
            raise ValueError("target host must be canonical")
        for value in self.addresses:
            if not is_public_address(value):
                raise ValueError("resolved target must contain public IPs only")
        if self.connected_ip is not None and self.connected_ip not in self.addresses:
            raise ValueError("connected IP differs from checked addresses")
        return self


class Header(Contract):
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
    ]
    value: str
    redacted: bool


class FetchAttempt(Contract):
    schema_version: SchemaVersion
    attempt_id: Identifier
    parent_attempt_id: Identifier | None
    requested_url: URL
    purpose: Literal["initial", "retry", "http_fallback", "redirect"]
    request_sent: bool
    resolved_target: ResolvedTarget | None
    status_code: int | None = Field(ge=100, le=599)
    response_headers: tuple[Header, ...]
    started_at: UTCDateTime
    elapsed_ms: float = Field(ge=0)
    transport_error: ErrorDetail | None
    tls_state: TLSState
    evidence_id: Identifier
    bytes_read: NonNegativeInt
    decoded_bytes: NonNegativeInt
    body_complete: bool

    @model_validator(mode="after")
    def consistency(self) -> Self:
        parsed = normalize_input(self.requested_url)
        if self.request_sent and self.resolved_target is None:
            raise ValueError("sent request requires checked target")
        if self.resolved_target is not None:
            port = urlsplit(self.requested_url).port
            expected_port = port or (443 if self.requested_url.startswith("https:") else 80)
            if (self.resolved_target.host, self.resolved_target.port) != (
                parsed.host_ascii,
                expected_port,
            ):
                raise ValueError("target does not match request authority")
        if self.status_code is None and self.transport_error is None:
            raise ValueError("attempt needs a response or a structured transport error")
        if self.status_code is not None and not self.request_sent:
            raise ValueError("unsent request cannot have an HTTP status")
        if self.status_code is None and (
            self.response_headers or self.body_complete or self.bytes_read
        ):
            raise ValueError("response data without an HTTP response")
        if self.transport_error is not None:
            if self.transport_error.category not in {"transport", "target", "budget", "content"}:
                raise ValueError("HTTP/content errors are not transport errors")
            if self.transport_error.attempt_id != self.attempt_id:
                raise ValueError("transport error must identify its attempt")
            if self.body_complete:
                raise ValueError("transport error cannot have complete body")
        if self.requested_url.startswith("http:") and self.tls_state != "not_applicable":
            raise ValueError("HTTP has no TLS observation")
        if self.requested_url.startswith("https:") and self.tls_state == "not_applicable":
            raise ValueError("HTTPS must track TLS")
        if self.status_code is not None and self.requested_url.startswith("https:"):
            if self.tls_state != "verified":
                raise ValueError("HTTPS response requires verified TLS")
        if self.purpose == "initial" and self.parent_attempt_id is not None:
            raise ValueError("initial attempt cannot have parent")
        if self.purpose != "initial" and self.parent_attempt_id is None:
            raise ValueError("derived attempt needs parent")
        return self


class RedirectHop(Contract):
    schema_version: SchemaVersion
    source_attempt_id: Identifier
    source_url: URL
    status_code: Literal[301, 302, 303, 307, 308]
    raw_location: str | None
    target_url: URL | None
    decision: Literal["follow", "allow_www_alias", "needs_input", "reject"]
    reason_code: ErrorCode | None
    observed_at: UTCDateTime
    psl_sha256: SHA256 | None

    @model_validator(mode="after")
    def destination(self) -> Self:
        if self.decision != "reject" and (not self.raw_location or self.target_url is None):
            raise ValueError("redirect requires valid Location and target")
        if self.decision == "allow_www_alias" and self.psl_sha256 is None:
            raise ValueError("automatic www alias requires local PSL hash")
        if self.decision in {"reject", "needs_input"} and self.reason_code is None:
            raise ValueError("stopped redirect requires reason")
        return self


class EvidenceFile(Contract):
    path: str
    sha256: SHA256
    size_bytes: NonNegativeInt
    representation: Literal[
        "content_encoded", "entity_bytes", "decoded_text", "attempt_observation"
    ]
    complete: bool

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or ":" in value
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != value
        ):
            raise ValueError("evidence path must be canonical and relative to run directory")
        devices = {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        if any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}", part)
            or part.endswith(".")
            or part.split(".")[0].upper() in devices
            for part in path.parts
        ):
            raise ValueError("invalid artifact component")
        return value


class EvidenceRecord(Contract):
    schema_version: SchemaVersion
    evidence_id: Identifier
    attempt_id: Identifier
    requested_url: URL
    captured_at: UTCDateTime
    files: tuple[EvidenceFile, ...] = Field(min_length=1)
    declared_encoding: str | None
    applied_encoding: str | None
    replacement_characters: NonNegativeInt
    sniffed_html: bool
    classification_basis: tuple[str, ...]
    classification_confidence: float | None = Field(ge=0, le=1)
    redacted_headers: tuple[str, ...]

    @model_validator(mode="after")
    def representations(self) -> Self:
        reps = [f.representation for f in self.files]
        if len(set(reps)) != len(reps):
            raise ValueError("one file per representation")
        if "decoded_text" in reps and not self.applied_encoding:
            raise ValueError("decoded text needs encoding provenance")
        if self.sniffed_html and not self.classification_basis:
            raise ValueError("sniffing requires recorded basis")
        return self


class SiteValidationResult(Contract):
    _fetch_result: object = PrivateAttr(default=None)
    _live_budget: object = PrivateAttr(default=None)
    _run_directory: object = PrivateAttr(default=None)

    def __eq__(self, other: object) -> bool:
        # Runtime handles/caches are outside serialized contract equality.
        return isinstance(other, SiteValidationResult) and self.__dict__ == other.__dict__

    schema_version: SchemaVersion
    algorithm_version: AlgorithmVersion
    config_version: ConfigVersion
    config: Step0Config
    config_sha256: SHA256
    run_id: Identifier
    parent_run_id: Identifier | None
    started_at: UTCDateTime
    finished_at: UTCDateTime
    source_timezone: str
    input_url: str
    normalized_url: URL | None
    input_scheme_explicit: bool | None
    input_host_unicode: str | None
    effective_url: URL | None
    origin: str | None
    entry_path: str | None
    allowed_hosts: tuple[str, ...]
    path_scope: tuple[str, ...]
    status: Status
    can_enter_step1: bool
    content_state: ContentState
    diagnostic_allowed: bool
    fallback_used: bool
    selected_evidence_id: Identifier | None
    final_attempt_id: Identifier | None
    attempt_ids: tuple[Identifier, ...]
    attempts: tuple[FetchAttempt, ...]
    redirect_chain: tuple[RedirectHop, ...]
    evidence: tuple[EvidenceRecord, ...]
    errors: tuple[ErrorDetail, ...]
    reason_codes: tuple[ErrorCode, ...]
    proposed_target_url: URL | None
    next_action: str = Field(min_length=1)
    budget: BudgetSnapshot

    @model_validator(mode="after")
    def consistency(self) -> Self:
        revision = self.schema_version.split(".")[1]
        if (
            self.algorithm_version != f"step0-1.{revision}"
            or self.config_version != f"step0-config-1.{revision}.0"
            or any(
                version != self.schema_version
                for version in (
                    self.config.schema_version,
                    self.budget.schema_version,
                    *(item.schema_version for item in self.attempts),
                    *(item.schema_version for item in self.redirect_chain),
                    *(item.schema_version for item in self.evidence),
                )
            )
        ):
            raise ValueError("mixed schema/algorithm/configuration versions")
        if self.config_sha256 != self.config.fingerprint():
            raise ValueError("config fingerprint mismatch")
        if self.source_timezone != self.config.source_timezone:
            raise ValueError("timezone differs from configuration")
        if self.finished_at < self.started_at or self.budget.started_at != self.started_at:
            raise ValueError("inconsistent run times")
        if self.budget.captured_at != self.finished_at:
            raise ValueError("budget must be captured at result completion")
        if self.path_scope != self.config.path_scope:
            raise ValueError("path scope differs from configuration")
        if len(set(self.allowed_hosts)) != len(self.allowed_hosts):
            raise ValueError("duplicate scope hosts")
        if any(normalize_host(h) != h for h in self.allowed_hosts):
            raise ValueError("scope hosts must be canonical")
        if self.normalized_url is None:
            if any(
                v is not None
                for v in (
                    self.input_scheme_explicit,
                    self.entry_path,
                    self.input_host_unicode,
                    self.effective_url,
                )
            ):
                raise ValueError("invalid input must not have normalized metadata")
        else:
            parsed = normalize_input(self.input_url)
            if (
                parsed.url,
                parsed.input_scheme_explicit,
                parsed.entry_path,
                parsed.host_unicode,
            ) != (
                self.normalized_url,
                self.input_scheme_explicit,
                self.entry_path,
                self.input_host_unicode,
            ):
                raise ValueError("normalized input metadata mismatch")
            expected_hosts = {parsed.host_ascii, *self.config.allowed_hosts}
            for hop in self.redirect_chain:
                if hop.decision == "allow_www_alias" and hop.target_url:
                    expected_hosts.add(normalize_input(hop.target_url).host_ascii)
            if set(self.allowed_hosts) != expected_hosts:
                raise ValueError("scope expanded without explicit host or observed alias")
        ids = tuple(a.attempt_id for a in self.attempts)
        if self.attempt_ids != ids or len(set(ids)) != len(ids):
            raise ValueError("attempt IDs must be unique and match attempts in order")
        observed_count = sum(a.request_sent for a in self.attempts)
        output_failure = self.status == "failed" and "OUTPUT_WRITE_FAILED" in self.reason_codes
        if observed_count != self.budget.step0_attempts and not (
            output_failure and observed_count <= self.budget.step0_attempts
        ):
            raise ValueError("attempt count differs from transport starts")
        if self.budget.step0_max_attempts != self.config.step0_max_attempts:
            raise ValueError("budget limit differs from config")
        if self.budget.total_max_attempts != self.config.total_max_attempts:
            raise ValueError("shared limit differs from config")
        if (
            self.budget.step0_deadline_at - self.started_at
        ).total_seconds() != self.config.step0_deadline_seconds:
            raise ValueError("step 0 deadline differs from config")
        if (
            self.budget.total_deadline_at - self.started_at
        ).total_seconds() != self.config.total_deadline_seconds:
            raise ValueError("shared deadline differs from config")
        seen: set[str] = set()
        evidence = {e.evidence_id: e for e in self.evidence}
        if len(evidence) != len(self.evidence):
            raise ValueError("duplicate evidence ID")
        for attempt in self.attempts:
            if attempt.parent_attempt_id is not None and attempt.parent_attempt_id not in seen:
                raise ValueError("attempt parent must precede child")
            seen.add(attempt.attempt_id)
            record = evidence.get(attempt.evidence_id)
            if record is None or (record.attempt_id, record.requested_url) != (
                attempt.attempt_id,
                attempt.requested_url,
            ):
                raise ValueError("missing or mismatched attempt evidence")
        if {e.attempt_id for e in self.evidence} - seen:
            raise ValueError("orphan evidence")
        if self.final_attempt_id is not None and self.final_attempt_id not in seen:
            raise ValueError("final attempt missing")
        if self.errors and tuple(dict.fromkeys(e.code for e in self.errors)) != self.reason_codes:
            raise ValueError("reason codes must match structured errors, in order")
        if not self.errors and self.reason_codes:
            raise ValueError("reason code without error detail")
        if any(e.attempt_id is not None and e.attempt_id not in seen for e in self.errors):
            raise ValueError("error references unknown attempt")
        for hop in self.redirect_chain:
            source = next((a for a in self.attempts if a.attempt_id == hop.source_attempt_id), None)
            if source is None or (source.requested_url, source.status_code) != (
                hop.source_url,
                hop.status_code,
            ):
                raise ValueError("redirect does not match observed response")
        if self.fallback_used != any(a.purpose == "http_fallback" for a in self.attempts):
            raise ValueError("fallback flag differs from attempt history")
        fallback_attempts = [a for a in self.attempts if a.purpose == "http_fallback"]
        if len(fallback_attempts) > 1:
            raise ValueError("only one HTTP fallback candidate may be started")
        if fallback_attempts:
            if (
                not self.config.allow_http_fallback
                or self.normalized_url is None
                or not self.normalized_url.startswith("https://")
            ):
                raise ValueError("fallback requires permitted initial HTTPS candidate")
            sibling = normalize_input("http://" + self.normalized_url[len("https://") :]).url
            if fallback_attempts[0].requested_url != sibling:
                raise ValueError("fallback must retain original host, path and query")
        if self.status in {"failed", "needs_input"} and self.can_enter_step1:
            raise ValueError("stopped run cannot enter step 1")
        fatal = any(e.category in {"input", "target", "output"} for e in self.errors)
        if fatal and self.status != "failed":
            raise ValueError("input/target/output errors require failed result")
        if self.status == "ok" and (
            not self.can_enter_step1
            or self.content_state != "html"
            or self.fallback_used
            or self.reason_codes
            or self.diagnostic_allowed
        ):
            raise ValueError("ok requires unrestricted usable HTML")
        if self.config_version != self.config.config_version:
            raise ValueError("configuration version mismatch")
        if self.status == "partial" and not self.reason_codes:
            raise ValueError("partial requires explicit reason")
        final = next((a for a in self.attempts if a.attempt_id == self.final_attempt_id), None)
        if self.status == "partial":
            if final is None or final.status_code is None or not 200 <= final.status_code < 300:
                raise ValueError("partial requires an observed 2xx response")
            if not self.can_enter_step1 and self.content_state not in {
                "access_barrier",
                "empty",
                "non_html",
                "suspicious_html",
                "incomplete",
                "too_large",
            }:
                raise ValueError("partial without entry requires unusable or uncertain content")
        http_states = {
            "http_denied": {401, 403},
            "http_missing": {404, 410},
            "http_unavailable": {408, 429, *range(500, 600)},
        }
        if self.content_state in http_states:
            if final is None or final.status_code not in http_states[self.content_state]:
                raise ValueError("HTTP content state differs from observed status")
            if self.status != "failed":
                raise ValueError("terminal HTTP errors require failed")
        if self.content_state == "not_received" and final and final.status_code is not None:
            raise ValueError("not_received cannot describe an HTTP response")
        if self.status in {"failed", "needs_input"} and not self.reason_codes:
            raise ValueError("stopped result requires reason")
        if self.can_enter_step1:
            if any(code in {"ACCESS_CHALLENGE", "ACCESS_LOGIN"} for code in self.reason_codes):
                raise ValueError("confirmed access barrier prevents entry")
            if self.content_state not in USABLE_STATES:
                raise ValueError("content is not eligible for step 1")
            if self.content_state == "suspicious_html" and not self.diagnostic_allowed:
                raise ValueError("suspicious HTML needs explicit diagnostic permission")
            selected = evidence.get(self.selected_evidence_id or "")
            selected_attempt = next(
                (a for a in self.attempts if selected and a.attempt_id == selected.attempt_id), None
            )
            if selected is None or selected_attempt is None:
                raise ValueError("selected response is missing")
            if self.schema_version == "1.2.0" and (
                {f.representation for f in selected.files}
                != {"content_encoded", "entity_bytes", "decoded_text", "attempt_observation"}
                or not all(f.complete for f in selected.files)
                or selected.applied_encoding is None
            ):
                raise ValueError("current selected response requires all complete representations")
            if not selected.classification_basis:
                raise ValueError("selected HTML requires recorded classification basis")
            if (
                self.content_state == "suspicious_html"
                and selected.classification_confidence is None
            ):
                raise ValueError("suspicious HTML requires classification confidence")
            if selected_attempt.attempt_id != self.final_attempt_id:
                raise ValueError("selected response must be final attempt")
            if (
                selected_attempt.transport_error
                or selected_attempt.status_code != 200
                or not selected_attempt.body_complete
            ):
                raise ValueError("selected HTML requires complete HTTP 200 without transport error")
            if not any(
                f.representation == "entity_bytes" and f.complete and f.size_bytes > 0
                for f in selected.files
            ):
                raise ValueError("selected HTML requires complete nonempty entity bytes")
            if self.effective_url != selected_attempt.requested_url:
                raise ValueError("effective URL differs from selected request")
            entity = next(f for f in selected.files if f.representation == "entity_bytes")
            if entity.size_bytes != selected_attempt.decoded_bytes:
                raise ValueError("decoded size differs from evidence")
            if self.origin != normalize_input(self.effective_url).origin:
                raise ValueError("origin differs from selected URL")
            if normalize_input(self.effective_url).host_ascii not in self.allowed_hosts:
                raise ValueError("selected host outside scope")
        elif any(
            v is not None for v in (self.selected_evidence_id, self.effective_url, self.origin)
        ):
            raise ValueError("non-eligible run cannot advertise selected HTML")
        if self.status == "needs_input" and self.proposed_target_url is None:
            raise ValueError("external transition requires proposed_target_url")
        if self.status == "needs_input" and not any(
            h.decision == "needs_input" and h.target_url == self.proposed_target_url
            for h in self.redirect_chain
        ):
            raise ValueError("proposed target requires an observed stopped redirect")
        if self.status != "needs_input" and self.proposed_target_url is not None:
            raise ValueError("proposed target belongs only to needs_input")
        if self.proposed_target_url is not None:
            if normalize_input(self.proposed_target_url).host_ascii in self.allowed_hosts:
                raise ValueError("proposed host is already allowed")
        if self.algorithm_version == "step0-1.2":
            from .policy import next_action_for

            host = (
                normalize_input(self.proposed_target_url).host_ascii
                if self.proposed_target_url
                else None
            )
            if self.next_action != next_action_for(
                status=self.status,
                content_state=self.content_state,
                reason_codes=self.reason_codes,
                proposed_host=host,
                can_enter_step1=self.can_enter_step1,
            ):
                raise ValueError("next_action contradicts the terminal state")
        return self


class Step1Input(Contract):
    schema_version: SchemaVersion
    source_step: Literal[0]
    site: SiteValidationResult
    selected_response: FetchAttempt
    selected_evidence: EvidenceRecord
    budget: BudgetSnapshot
    crawl_permission: Literal["unknown"]

    @model_validator(mode="after")
    def handoff(self) -> Self:
        if self.schema_version != self.site.schema_version:
            raise ValueError("handoff schema differs from source result")
        if not self.site.can_enter_step1:
            raise ValueError("step 0 did not permit entry")
        if self.selected_evidence.evidence_id != self.site.selected_evidence_id:
            raise ValueError("selected evidence ID mismatch")
        if (
            self.selected_evidence not in self.site.evidence
            or self.selected_response not in self.site.attempts
        ):
            raise ValueError("handoff must reuse exact recorded response and evidence")
        if self.selected_response.evidence_id != self.selected_evidence.evidence_id:
            raise ValueError("selected response/evidence mismatch")
        if self.budget != self.site.budget:
            raise ValueError("handoff must not reset shared budget")
        if self.budget.captured_at >= self.budget.total_deadline_at:
            raise ValueError("shared deadline already exhausted")
        if self.budget.total_attempts >= self.budget.total_max_attempts:
            raise ValueError("shared attempt budget exhausted")
        return self
