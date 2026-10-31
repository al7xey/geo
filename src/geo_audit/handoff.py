"""Validated two-mode handoff. Loading never performs HTTP/DNS or renews a deadline."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter, ValidationError

from .api import FetchResult
from .budget import Budget
from .errors import GeoAuditError, problem
from .evidence import EvidenceStore
from .models import EvidenceRecord, SiteValidationResult, Step1Input
from .psl import load_bundled_psl
from .scope import build_scope, decide_redirect, scope_reason
from .timing import TimeServices
from .urls import normalize_input

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class Step1Handoff:
    contract: Step1Input
    fetch_result: FetchResult
    budget: Budget

    @property
    def html(self) -> bytes:
        return self.fetch_result.entity_bytes


def _contract(site: SiteValidationResult) -> Step1Input:
    if not site.can_enter_step1:
        raise problem("INPUT_ARTIFACT_INVALID", "input", "Step 0 did not permit step 1.")
    record = next(e for e in site.evidence if e.evidence_id == site.selected_evidence_id)
    response = next(a for a in site.attempts if a.evidence_id == record.evidence_id)
    return Step1Input(
        schema_version="1.2.0",
        source_step=0,
        site=site,
        selected_response=response,
        selected_evidence=record,
        budget=site.budget,
        crawl_permission="unknown",
    )


def _scope_history(site: SiteValidationResult) -> None:
    if site.normalized_url is None:
        return
    scope = build_scope(
        normalize_input(site.input_url),
        allowed_hosts=site.config.allowed_hosts,
        path_scope=site.path_scope,
        allowed_extra_ports=site.config.allowed_extra_ports,
    )
    psl = load_bundled_psl()
    index = {a.attempt_id: n for n, a in enumerate(site.attempts)}
    for n, hop in enumerate(site.redirect_chain):
        previous = site.attempts[: index[hop.source_attempt_id] + 1]
        failed = (
            frozenset(
                a.requested_url
                for a in previous
                if a.transport_error and a.requested_url.startswith("https:")
            )
            if site.fallback_used
            else frozenset()
        )
        expected = decide_redirect(
            source=normalize_input(hop.source_url),
            status_code=hop.status_code,
            location=hop.raw_location,
            scope=scope,
            psl=psl,
            visited=frozenset(a.requested_url for a in previous),
            failed_https=failed,
            hops=n,
            max_hops=site.config.max_redirect_hops,
        )
        if (
            hop.decision == "reject"
            and hop.reason_code
            and hop.reason_code.startswith(("TARGET_", "DNS_"))
        ):
            if expected.target is None or expected.target.url != hop.target_url:
                raise ValueError("rejected destination mismatch")
            continue
        if (
            expected.action,
            expected.target.url if expected.target else None,
            expected.psl_sha256,
        ) != (hop.decision, hop.target_url, hop.psl_sha256):
            raise ValueError("redirect decision or PSL provenance mismatch")
        scope = expected.scope
    if frozenset(site.allowed_hosts) != scope.allowed_hosts:
        raise ValueError("allowed host expansion lacks a valid observed redirect")
    for attempt in site.attempts:
        if attempt.request_sent and scope_reason(normalize_input(attempt.requested_url), scope):
            raise ValueError("request outside its recorded scope")


def load_site_result(
    run_directory: Path, *, expected_config_hash: str | None = None, verify_files: bool = True
) -> SiteValidationResult:
    """Inspect any terminal state. Positive handoff has additional freshness/byte checks below."""
    store = EvidenceStore(run_directory)
    try:
        raw_site = store.read_json("site.json")
        site = SiteValidationResult.model_validate_json(json.dumps(raw_site, allow_nan=False))
        if site.schema_version == "1.2.0" and site.run_id != store.root.name:
            raise ValueError("run directory identity mismatch")
        if expected_config_hash is not None and site.config_sha256 != expected_config_hash:
            raise ValueError("unexpected configuration")
        manifest = TypeAdapter(tuple[EvidenceRecord, ...]).validate_json(
            json.dumps(store.read_json("manifest.json"), allow_nan=False)
        )
        if manifest != site.evidence:
            raise ValueError("manifest differs from result")
        if site.schema_version == "1.2.0":
            if store.read_json("config.json") != site.config.model_dump(mode="json"):
                raise ValueError("configuration differs from committed result")
            if store.read_json("budget.json") != site.budget.model_dump(mode="json"):
                raise ValueError("budget differs from committed result")
        sizes: dict[str, int] = {}
        for record in manifest:
            for file in record.files:
                if not file.path.startswith("raw/") or len(Path(file.path).parts) != 2:
                    raise ValueError("invalid evidence path")
                if file.size_bytes > MAX_FILE_BYTES or (
                    file.path in sizes and sizes[file.path] != file.size_bytes
                ):
                    raise ValueError("invalid evidence size")
                sizes[file.path] = file.size_bytes
        if sum(sizes.values()) > MAX_TOTAL_BYTES:
            raise ValueError("run exceeds the safe loading budget")
        _scope_history(site)
        if verify_files:
            for record in manifest:
                store.verify(record)
        return site
    except (ValueError, ValidationError, KeyError, StopIteration, GeoAuditError, RecursionError):
        raise problem(
            "INPUT_ARTIFACT_INVALID", "input", "Saved result/manifest is invalid or incomplete."
        ) from None


def build_step1_input(
    site: SiteValidationResult, store: EvidenceStore, *, now: datetime | None = None
) -> Step1Input:
    site = SiteValidationResult.model_validate_json(site.model_dump_json())
    value = _contract(site)
    verify_step1_input(value, store, now=now)
    return value


def verify_step1_input(
    value: Step1Input, store: EvidenceStore, *, now: datetime | None = None
) -> None:
    try:
        checked = Step1Input.model_validate_json(value.model_dump_json())
        current = now or datetime.now(UTC)
        if current < checked.budget.captured_at or current >= checked.budget.total_deadline_at:
            raise ValueError("expired budget or reversed UTC clock")
        for record in checked.site.evidence:
            store.verify(record)
    except (ValueError, GeoAuditError):
        raise problem(
            "INPUT_ARTIFACT_INVALID", "input", "Handoff evidence or shared deadline is invalid."
        ) from None


def prepare_step1(
    site: SiteValidationResult, *, budget: Budget | None = None, timing: TimeServices | None = None
) -> Step1Handoff:
    """In-process path keeps exactly the same FetchResult and live Budget objects."""
    value = _contract(site)
    fetch = site._fetch_result
    shared = budget or site._live_budget
    if not isinstance(fetch, FetchResult) or not isinstance(shared, Budget):
        raise problem(
            "INPUT_ARTIFACT_INVALID", "input", "Use the saved-run loader for detached results."
        )
    if shared is not site._live_budget or shared.config != site.config:
        raise problem(
            "INPUT_ARTIFACT_INVALID",
            "input",
            "In-process handoff cannot replace the shared budget.",
        )
    try:
        shared.check(step=1)
    except GeoAuditError:
        raise problem(
            "INPUT_ARTIFACT_INVALID", "input", "Shared budget exhausted; start a new run."
        ) from None
    directory = cast(Path, site._run_directory)
    verify_step1_input(value, EvidenceStore(directory), now=(timing or TimeServices()).utcnow())
    return Step1Handoff(value, fetch, shared)


def load_step1_input(
    run_directory: Path,
    *,
    timing: TimeServices | None = None,
    expected_config_hash: str | None = None,
) -> Step1Handoff:
    clock = timing or TimeServices()
    store = EvidenceStore(run_directory)
    site = load_site_result(
        run_directory, expected_config_hash=expected_config_hash, verify_files=False
    )
    try:
        contract = _contract(site)
        budget = Budget.restore(site.config, site.budget, timing=clock)  # Before reading any body.
        verify_step1_input(contract, store, now=clock.utcnow())
        record = contract.selected_evidence
        files = {f.representation: f for f in record.files}
        entity = store.verified_bytes(
            files["entity_bytes"], limit=min(MAX_FILE_BYTES, site.config.max_decoded_bytes)
        )
        encoded = store.verified_bytes(
            files["content_encoded"], limit=min(MAX_FILE_BYTES, site.config.max_encoded_bytes)
        )
        decoded = store.verified_bytes(
            files["decoded_text"], limit=min(MAX_FILE_BYTES, site.config.max_decoded_bytes * 4)
        ).decode("utf-8")
        if (
            not all(f.complete for f in files.values())
            or not entity
            or record.applied_encoding is None
        ):
            raise ValueError("selected HTML is incomplete")
        from .content import ContentAssessment

        assessment = ContentAssessment(
            site.content_state,
            record.classification_basis,
            record.classification_confidence or 0.0,
            decoded,
            record.declared_encoding,
            record.applied_encoding,
            record.replacement_characters,
            record.sniffed_html,
            site.diagnostic_allowed,
            (),
        )
        fetch = FetchResult(
            contract.selected_response, record, encoded, entity, decoded, assessment
        )
        budget.check(step=1)
        return Step1Handoff(contract, fetch, budget)
    except (GeoAuditError, ValueError, KeyError, UnicodeError):
        raise problem(
            "INPUT_ARTIFACT_INVALID",
            "input",
            "Cannot load selected HTML; no empty substitute is allowed.",
        ) from None
