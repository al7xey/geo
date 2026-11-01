"""Validated two-mode handoff. Loading never performs HTTP/DNS or renews a deadline."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter, ValidationError

from .api import FetchResult
from .body import LimitedBody
from .budget import Budget
from .errors import GeoAuditError, problem
from .evidence import MAX_FILE_BYTES, MAX_JSON_BYTES, EvidenceStore, verify_evidence_limits
from .http_client import HttpAttempt
from .models import EvidenceRecord, SiteValidationResult, Step1Input
from .policy import choose_next_action
from .psl import load_bundled_psl
from .scope import build_scope, decide_redirect, scope_reason
from .timing import TimeServices
from .urls import normalize_input


def _verify_observations(site: SiteValidationResult, store: EvidenceStore) -> None:
    if site.schema_version != "1.2.0":
        return
    for record in site.evidence:
        observation = next(f for f in record.files if f.representation == "attempt_observation")
        raw = store.verified_bytes(observation, limit=MAX_JSON_BYTES)
        attempt = next(a for a in site.attempts if a.attempt_id == record.attempt_id)
        if json.loads(raw) != attempt.model_dump(mode="json"):
            raise ValueError("attempt observation differs from site.json")


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
        schema_version=site.schema_version,
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
    hops = {h.source_attempt_id: h for h in site.redirect_chain}
    if len(hops) != len(site.redirect_chain):
        raise ValueError("duplicate redirect observation")
    visited: set[str] = set()
    failed: set[str] = set()
    next_url: str | None = site.normalized_url
    previous = None
    retries = hop_count = 0
    fallback = False
    observed_hops = []
    for attempt in site.attempts:
        if previous is None:
            if attempt.purpose != "initial" or attempt.requested_url != next_url:
                raise ValueError("initial request differs from input")
        elif attempt.parent_attempt_id != previous.attempt_id:
            raise ValueError("attempt parent must be the preceding observation")
        elif attempt.purpose == "redirect":
            hop = hops.get(previous.attempt_id)
            if hop is None or hop.decision not in {"follow", "allow_www_alias"}:
                raise ValueError("request after an unapproved redirect")
            retries = 0
        else:
            action = choose_next_action(
                initial_url=site.normalized_url,
                current_url=previous.requested_url,
                status_code=previous.status_code,
                error_code=previous.transport_error.code if previous.transport_error else None,
                allow_fallback=site.config.allow_http_fallback,
                fallback_used=fallback,
                retries_used=retries,
                max_retries=site.config.max_retries,
                budget_available=True,
            )
            if action.action != attempt.purpose:
                raise ValueError("retry/fallback lacks a permitted cause")
            if attempt.purpose == "http_fallback":
                failed.add(previous.requested_url)
                next_url = normalize_input("http://" + site.normalized_url[len("https://") :]).url
                fallback, retries = True, 0
            else:
                retries += 1
                next_url = previous.requested_url
        if attempt.requested_url != next_url:
            raise ValueError("request differs from the approved destination")
        if attempt.request_sent and scope_reason(normalize_input(attempt.requested_url), scope):
            raise ValueError("request outside its scope at the time of sending")
        visited.add(attempt.requested_url)
        previous = attempt
        hop = hops.get(attempt.attempt_id)
        if hop is None:
            continue
        observed_hops.append(hop)
        locations = [h.value for h in attempt.response_headers if h.name == "location"]
        if hop.raw_location != (locations[0] if len(locations) == 1 else None):
            raise ValueError("redirect differs from recorded Location headers")
        expected = decide_redirect(
            source=normalize_input(hop.source_url),
            status_code=hop.status_code,
            location=hop.raw_location,
            scope=scope,
            psl=psl,
            visited=frozenset(visited),
            failed_https=frozenset(failed),
            hops=hop_count,
            max_hops=site.config.max_redirect_hops,
        )
        hop_count += 1
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
            expected.reason_code,
        ) != (hop.decision, hop.target_url, hop.psl_sha256, hop.reason_code):
            raise ValueError("redirect decision or PSL provenance mismatch")
        scope = expected.scope
        next_url = hop.target_url
    if observed_hops != list(site.redirect_chain):
        raise ValueError("redirect history is not chronological")
    if frozenset(site.allowed_hosts) != scope.allowed_hosts:
        raise ValueError("allowed host expansion lacks a valid observed redirect")


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
            json.dumps(
                store.read_json(
                    "raw/manifest.json" if site.schema_version == "1.2.0" else "manifest.json"
                ),
                allow_nan=False,
            )
        )
        if manifest != site.evidence:
            raise ValueError("manifest differs from result")
        if site.schema_version == "1.2.0":
            if store.read_json("config.json") != site.config.model_dump(mode="json"):
                raise ValueError("configuration differs from committed result")
            if store.read_json("budget.json") != site.budget.model_dump(mode="json"):
                raise ValueError("budget differs from committed result")
        verify_evidence_limits(manifest)
        _scope_history(site)
        if verify_files:
            for record in manifest:
                store.verify(record)
            _verify_observations(site, store)
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
        verify_evidence_limits(checked.site.evidence)
        current = now or datetime.now(UTC)
        if current < checked.budget.captured_at or current >= checked.budget.total_deadline_at:
            raise ValueError("expired budget or reversed UTC clock")
        _scope_history(checked.site)
        for record in checked.site.evidence:
            store.verify(record)
        _verify_observations(checked.site, store)
    except (ValueError, StopIteration, GeoAuditError):
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
    if fetch.response != value.selected_response or fetch.evidence != value.selected_evidence:
        raise problem("INPUT_ARTIFACT_INVALID", "input", "Live response differs from the contract.")
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
        response = contract.selected_response
        encodings = [h.value for h in response.response_headers if h.name == "content-encoding"]
        body = LimitedBody(",".join(encodings), len(encoded), len(entity))
        body.feed(encoded)
        body.finish()
        if body.decoded != entity or len(encoded) != response.bytes_read:
            raise ValueError("encoded and entity representations differ")
        from .content import assess_response

        assessment = assess_response(
            HttpAttempt(
                response.attempt_id,
                response.parent_attempt_id,
                response.purpose,
                response.requested_url,
                response.started_at,
                response.elapsed_ms,
                response.request_sent,
                None,
                None,
                response.tls_state,
                response.status_code,
                response.response_headers,
                encoded,
                entity,
                response.bytes_read,
                response.body_complete,
                response.transport_error,
            ),
            sniff_bytes=site.config.sniff_bytes,
        )
        if (
            assessment.text != decoded
            or assessment.state != site.content_state
            or assessment.applied_encoding != record.applied_encoding
            or assessment.declared_encoding != record.declared_encoding
            or assessment.replacements != record.replacement_characters
            or assessment.sniffed != record.sniffed_html
            or assessment.basis != record.classification_basis
            or assessment.confidence != record.classification_confidence
            or assessment.diagnostic_allowed != site.diagnostic_allowed
        ):
            raise ValueError("selected text/classification differs from original HTML")
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
