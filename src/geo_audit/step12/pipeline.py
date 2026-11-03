"""Final publication has reproducible HTML, data and explicit control results."""

from pathlib import Path

from ..evidence import EvidenceStore
from ..step5.pipeline import MAX_METADATA_BYTES, encoded
from ..step9 import storage
from ..step9.models import Aggregates, SiteContext
from ..step9.pipeline import json_model
from ..step10.models import Gaps
from ..step11.models import Action, Finding, Findings, Plan
from ..step11.pipeline import validate_basis
from ..step11.pipeline import verify_publication as verify_step11
from .models import ReportData
from .report import audit_html, render

SOURCE_FILES = ("aggregates.json", "context.json", "gaps.json", "findings.json", "plan.json")
INPUT_FILES = (*SOURCE_FILES, "source-manifest.json")


def build_artifacts(inputs: dict[str, bytes]) -> dict[str, bytes]:
    if set(inputs) != set(INPUT_FILES):
        raise ValueError("unexpected report inputs")
    storage.bind(inputs, 11, SOURCE_FILES)
    aggregate = json_model(inputs["aggregates.json"], Aggregates)
    context = json_model(inputs["context.json"], SiteContext)
    gaps = json_model(inputs["gaps.json"], Gaps)
    findings = json_model(inputs["findings.json"], Findings)
    plan = json_model(inputs["plan.json"], Plan)
    based_items: list[Finding | Action] = [*findings.items, *plan.actions]
    for item in based_items:
        for basis in item.basis:
            validate_basis(basis, aggregate, context, gaps)
    html, redacted = render(aggregate, context, gaps, findings, plan)
    references = audit_html(html.decode(), aggregate)
    report_data = ReportData(
        aggregates=aggregate,
        findings=findings,
        plan=plan,
        gaps=gaps,
        site_context_status=context.status,
    )
    files = {
        name: inputs[name]
        for name in ("aggregates.json", "findings.json", "plan.json", "gaps.json")
    }
    files.update(
        {
            "report.html": html,
            "report_data.json": encoded(report_data.model_dump(mode="json")),
            "validation.json": encoded(
                {
                    "numeric_references": references,
                    "all_numeric_displays_match_aggregates": True,
                    "service_markers_absent": True,
                    "redacted_fragments": redacted,
                    "empty_sections_explain_missingness": True,
                    "network_requests": 0,
                    "model_calls": 0,
                }
            ),
        }
    )
    return storage.artifacts(inputs, files)


def run_step12(step11_dir: Path, output_dir: Path) -> ReportData:
    storage.separate(step11_dir, output_dir)
    verify_step11(step11_dir)
    source = EvidenceStore(step11_dir)
    inputs = {name: source.read_bytes(name, limit=MAX_METADATA_BYTES) for name in SOURCE_FILES}
    inputs["source-manifest.json"] = source.read_bytes("manifest.json", limit=MAX_METADATA_BYTES)
    files = storage.publish(inputs, output_dir, 12, build_artifacts)
    return json_model(files["report_data.json"], ReportData)


def verify_publication(directory: Path) -> ReportData:
    files = storage.verify(directory, 12, INPUT_FILES, build_artifacts)
    return json_model(files["report_data.json"], ReportData)
