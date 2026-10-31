"""Build and verify the serializable handoff without fetching the HTML again."""

from datetime import UTC, datetime

from .errors import problem
from .evidence import EvidenceStore
from .models import SiteValidationResult, Step1Input


def build_step1_input(
    site: SiteValidationResult,
    store: EvidenceStore,
    *,
    now: datetime | None = None,
) -> Step1Input:
    site = SiteValidationResult.model_validate_json(site.model_dump_json())
    if not site.can_enter_step1:
        raise problem("INPUT_ARTIFACT_INVALID", "input", "Step 0 did not permit step 1.")
    record = next(e for e in site.evidence if e.evidence_id == site.selected_evidence_id)
    response = next(a for a in site.attempts if a.evidence_id == record.evidence_id)
    value = Step1Input(
        schema_version="1.0.0",
        source_step=0,
        site=site,
        selected_response=response,
        selected_evidence=record,
        budget=site.budget,
        crawl_permission="unknown",
    )
    verify_step1_input(value, store, now=now)
    return value


def verify_step1_input(
    value: Step1Input,
    store: EvidenceStore,
    *,
    now: datetime | None = None,
) -> None:
    checked = Step1Input.model_validate_json(value.model_dump_json())
    current = now or datetime.now(UTC)
    if current < checked.budget.captured_at or current >= checked.budget.total_deadline_at:
        raise problem(
            "INPUT_ARTIFACT_INVALID", "input", "Shared budget is expired or clock is inconsistent."
        )
    store.verify(checked.selected_evidence)
