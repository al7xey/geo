"""Evidence-first step 0 orchestration. Known failures are data; programming defects raise."""

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

import anyio

from .budget import Budget
from .config import Step0Config
from .content import ContentAssessment, assess_response
from .errors import ErrorDetail, GeoAuditError, error_detail
from .evidence import EvidenceStore
from .http_client import FetchTrace, HttpAttempt, SafeHttpClient
from .models import (
    EvidenceFile,
    EvidenceRecord,
    FetchAttempt,
    RedirectHop,
    ResolvedTarget,
    SiteValidationResult,
)
from .network import Step0Client
from .policy import classify_result, next_action_for
from .scope import build_scope
from .timing import TimeServices
from .urls import NormalizedURL, normalize_input


@dataclass(frozen=True)
class FetchResult:
    """Ready selected HTML for in-process step 1. Bytes are not re-fetched or re-decoded."""

    response: FetchAttempt
    evidence: EvidenceRecord
    content_encoded: bytes
    entity_bytes: bytes
    html_text: str
    assessment: ContentAssessment


async def validate_site(
    input_url: str,
    config: Step0Config,
    client: Step0Client | None = None,
    budget: Budget | None = None,
    *,
    store: EvidenceStore | None = None,
    output_dir: Path = Path("output/runs"),
    timing: TimeServices | None = None,
    parent_run_id: str | None = None,
) -> SiteValidationResult:
    config = Step0Config.model_validate_json(config.model_dump_json())
    if len(input_url) > 16384:
        raise GeoAuditError(
            error_detail("INPUT_URL_INVALID", message="URL exceeds the input size limit.")
        )
    clock = timing or TimeServices()
    shared = budget or Budget(config, timing=clock)
    if shared.config != config or shared.snapshot().step0_attempts:
        raise ValueError("step 0 needs its original, unused shared budget and matching config")
    run_id = store.root.name if store else "run-" + uuid.uuid4().hex
    from pydantic import TypeAdapter

    from .contracts import Identifier

    TypeAdapter(Identifier).validate_python(run_id)
    if parent_run_id is not None:
        TypeAdapter(Identifier).validate_python(parent_run_id)
    storage = store or EvidenceStore(output_dir / run_id)
    attempts: list[FetchAttempt] = []
    evidence: list[EvidenceRecord] = []
    redirects: list[RedirectHop] = []
    observed: dict[str, tuple[HttpAttempt, ContentAssessment]] = {}
    entry: NormalizedURL | None = None
    trace: FetchTrace | None = None
    terminal: ErrorDetail | None = None
    prepared = False
    cancelled = False
    http = client

    def checkpoint(state: str) -> None:
        storage.checkpoint(
            {
                "attempts": [a.model_dump(mode="json") for a in attempts],
                "redirect_chain": [h.model_dump(mode="json") for h in redirects],
            },
            tuple(evidence),
            state=state,
        )

    async def observe(event: HttpAttempt | RedirectHop) -> None:
        if isinstance(event, RedirectHop):
            redirects.append(event)
            checkpoint("running")
            return
        assessment = assess_response(event, sniff_bytes=config.sniff_bytes)
        evidence_id = "ev-" + uuid.uuid4().hex
        target = (
            ResolvedTarget(
                host=event.target.host,
                port=event.target.port,
                addresses=event.target.addresses,
                connected_ip=event.connected_ip,
                enforcement="pinned",
            )
            if event.target
            else None
        )
        transport_error = event.error
        model = FetchAttempt(
            schema_version="1.2.0",
            attempt_id=event.attempt_id,
            parent_attempt_id=event.parent_attempt_id,
            requested_url=event.requested_url,
            purpose=event.purpose,
            request_sent=event.request_sent,
            resolved_target=target,
            status_code=event.status_code,
            response_headers=event.response_headers,
            started_at=event.started_at,
            elapsed_ms=event.elapsed_ms,
            transport_error=transport_error,
            tls_state=event.tls_state,
            evidence_id=evidence_id,
            bytes_read=event.received_bytes,
            decoded_bytes=len(event.decoded_body),
            body_complete=event.body_complete,
        )
        files: list[EvidenceFile] = []
        if event.status_code is not None:
            files.append(
                storage.write_blob(
                    event.encoded_body,
                    representation="content_encoded",
                    complete=event.body_complete,
                )
            )
            files.append(
                storage.write_blob(
                    event.decoded_body, representation="entity_bytes", complete=event.body_complete
                )
            )
            if assessment.text is not None:
                files.append(
                    storage.write_blob(
                        assessment.text.encode("utf-8"),
                        representation="decoded_text",
                        complete=event.body_complete,
                    )
                )
        files.append(
            storage.write_blob(
                model.model_dump_json().encode("utf-8"),
                representation="attempt_observation",
                complete=True,
            )
        )
        record = EvidenceRecord(
            schema_version="1.2.0",
            evidence_id=evidence_id,
            attempt_id=event.attempt_id,
            requested_url=event.requested_url,
            captured_at=clock.utcnow(),
            files=tuple(files),
            declared_encoding=assessment.declared_encoding,
            applied_encoding=assessment.applied_encoding,
            replacement_characters=assessment.replacements,
            sniffed_html=assessment.sniffed,
            classification_basis=assessment.basis,
            classification_confidence=assessment.confidence,
            redacted_headers=("set-cookie", "cookie", "authorization", "proxy-authorization"),
        )
        attempts.append(model)
        evidence.append(record)
        observed.clear()  # Only the final candidate can be handed off; prior evidence is on disk.
        observed[model.attempt_id] = (event, assessment)
        checkpoint("running")

    try:
        storage.prepare(new=True)
        prepared = True
        storage.atomic_json("config.json", config.model_dump(mode="json"))
        checkpoint("running")
        entry = normalize_input(input_url)
        scope = build_scope(
            entry,
            allowed_hosts=config.allowed_hosts,
            path_scope=config.path_scope,
            allowed_extra_ports=config.allowed_extra_ports,
        )
        http = client or SafeHttpClient(config, timing=clock)
        trace = await http.get(
            input_url,
            scope=scope,
            budget=shared,
            allow_http_fallback=config.allow_http_fallback,
            observer=observe,
        )
        # Adapters must deliver each completed observation exactly once, before returning.
        if tuple(a.attempt_id for a in attempts) != tuple(a.attempt_id for a in trace.attempts):
            if trace.error is None or trace.error.category != "output":
                raise RuntimeError("HTTP adapter did not persist the complete observed history")
        terminal = trace.error
    except asyncio.CancelledError:
        cancelled = True
        terminal = error_detail("NETWORK_CANCELLED")
        if isinstance(http, SafeHttpClient):
            trace = http.last_trace
    except GeoAuditError as caught:
        terminal = caught.detail

    def result_for(error: ErrorDetail | None) -> SiteValidationResult:
        last = attempts[-1] if attempts else None
        chosen = observed.get(last.attempt_id) if last else None
        assessment = chosen[1] if chosen else None
        errors = [a.transport_error for a in attempts if a.transport_error is not None]
        for a in attempts[:-1]:
            if a.transport_error is None and a.status_code in {408, 429, 500, 502, 503, 504}:
                errors.append(
                    error_detail(
                        "HTTP_UNAVAILABLE",
                        attempt_id=a.attempt_id,
                        message="An earlier HTTP attempt required a retry.",
                    )
                )
        fallback = any(a.purpose == "http_fallback" for a in attempts)
        downgrade = (
            trace.downgraded
            if trace
            else any(
                h.source_url.startswith("https:") and (h.target_url or "").startswith("http:")
                for h in redirects
            )
        )
        if assessment:
            errors.extend(
                error_detail(code, attempt_id=last.attempt_id if last else None)
                for code in assessment.reasons
            )
        if error:
            errors.append(error)
        state = assessment.state if assessment else "not_received"
        decision = classify_result(
            status_code=last.status_code if last else None,
            content_state=state,
            body_complete=last.body_complete if last else False,
            evidence_ready=True,
            terminal_error=error.code if error else None,
            fallback_used=fallback,
            downgraded=downgrade,
            diagnostic_allowed=assessment.diagnostic_allowed if assessment else False,
            warning_codes=tuple(e.code for e in errors),
        )
        if cancelled:
            decision = type(decision)("failed", False, "NETWORK_CANCELLED")
        if decision.reason_code:
            errors.append(
                error_detail(decision.reason_code, attempt_id=last.attempt_id if last else None)
            )
        # Dedupe complete details by code/attempt, retaining explicit chronological reasons.
        unique: dict[tuple[str, str | None], ErrorDetail] = {}
        for item in errors:
            if item.attempt_id is not None and item.attempt_id not in {
                a.attempt_id for a in attempts
            }:
                item = item.model_copy(update={"attempt_id": None})
            unique.setdefault((item.code, item.attempt_id), item)
        errors = list(unique.values())
        reasons = tuple(dict.fromkeys(e.code for e in errors))
        proposed = (
            next((h.target_url for h in reversed(redirects) if h.decision == "needs_input"), None)
            if decision.status == "needs_input"
            else None
        )
        proposed_host = normalize_input(proposed).host_ascii if proposed else None
        snapshot = shared.snapshot()
        hosts = (
            tuple(sorted(trace.scope.allowed_hosts))
            if trace
            else tuple(sorted({entry.host_ascii, *config.allowed_hosts}))
            if entry
            else ()
        )
        if trace is None:
            hosts = tuple(
                sorted(
                    {
                        *hosts,
                        *(
                            normalize_input(h.target_url).host_ascii
                            for h in redirects
                            if h.decision == "allow_www_alias" and h.target_url
                        ),
                    }
                )
            )
        value = SiteValidationResult(
            schema_version="1.2.0",
            algorithm_version="step0-1.2",
            config_version=config.config_version,
            config=config,
            config_sha256=config.fingerprint(),
            run_id=run_id,
            parent_run_id=parent_run_id,
            started_at=shared.started_at,
            finished_at=snapshot.captured_at,
            source_timezone=config.source_timezone,
            input_url=input_url,
            normalized_url=entry.url if entry else None,
            input_scheme_explicit=entry.input_scheme_explicit if entry else None,
            input_host_unicode=entry.host_unicode if entry else None,
            effective_url=last.requested_url if last and decision.can_enter_step1 else None,
            origin=normalize_input(last.requested_url).origin
            if last and decision.can_enter_step1
            else None,
            entry_path=entry.entry_path if entry else None,
            allowed_hosts=hosts,
            path_scope=config.path_scope,
            status=decision.status,
            can_enter_step1=decision.can_enter_step1,
            content_state=state,
            diagnostic_allowed=assessment.diagnostic_allowed if assessment else False,
            fallback_used=fallback,
            selected_evidence_id=last.evidence_id if last and decision.can_enter_step1 else None,
            final_attempt_id=last.attempt_id if last else None,
            attempt_ids=tuple(a.attempt_id for a in attempts),
            attempts=tuple(attempts),
            redirect_chain=tuple(redirects),
            evidence=tuple(evidence),
            errors=tuple(errors),
            reason_codes=reasons,
            proposed_target_url=proposed,
            next_action=next_action_for(
                status=decision.status,
                content_state=state,
                reason_codes=reasons,
                proposed_host=proposed_host,
                can_enter_step1=decision.can_enter_step1,
            ),
            budget=snapshot,
        )
        value._live_budget = shared
        value._run_directory = storage.root
        if decision.can_enter_step1 and chosen and last and assessment:
            value._fetch_result = FetchResult(
                last,
                evidence[-1],
                chosen[0].encoded_body,
                chosen[0].decoded_body,
                assessment.text or "",
                assessment,
            )
        return value

    result = result_for(terminal)
    if prepared:
        try:
            with anyio.CancelScope(shield=True):
                checkpoint("cancelled" if cancelled else "completed")
                storage.publish(result)
        except GeoAuditError:
            result = result_for(error_detail("OUTPUT_WRITE_FAILED"))
            # A permanently unwritable disk returns failure in memory, never a positive commit.
            try:
                storage.publish(result)
            except GeoAuditError:
                pass
    if cancelled:
        raise asyncio.CancelledError
    return result
