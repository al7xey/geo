"""Verified step 6 input, blind jobs and new-only reproducible review publications."""

from pathlib import Path
from typing import Literal

from ..evidence import MAX_TOTAL_BYTES, EvidenceStore
from ..secure_io import pinned_directory
from ..step5.models import Answer, PromptSet, Target
from ..step5.models import PublicationFile as FileDigest
from ..step5.pipeline import MAX_ANSWERS_BYTES, MAX_METADATA_BYTES, digest, encoded, unique_json
from ..step5.replay import reuse_replay
from ..step6.models import Annotation
from ..step6.models import PublicationManifest as Step6Manifest
from ..step6.pipeline import INPUT_FILES as STEP6_INPUT_FILES
from ..step6.pipeline import build_artifacts as build_step6
from ..step6.pipeline import verify_publication as verify_step6
from .evaluate import agreements, resolve
from .models import PublicationManifest, ReviewPolicy, Step7Summary
from .report import render_report
from .review import accept_results, select_jobs

SOURCE_FILES = (*STEP6_INPUT_FILES, "step6-manifest.json", "annotations.jsonl")
INPUT_FILES = (*SOURCE_FILES, "policy.json", "review-results.jsonl")


def has_rank_gap(row: dict[str, object]) -> bool:
    fields = row["outstanding_fields"]
    return isinstance(fields, list) and any(
        isinstance(field, str) and "rank_outside_review_scope" in field for field in fields
    )


def read_file(path: Path, limit: int) -> bytes:
    return EvidenceStore(path.parent).read_bytes(path.name, limit=limit)


@reuse_replay
def build_artifacts(snapshots: dict[str, bytes]) -> tuple[dict[str, bytes], Step7Summary]:
    manifest = Step6Manifest.model_validate(unique_json(snapshots["step6-manifest.json"]))
    primary_snapshots = {name: snapshots[name] for name in STEP6_INPUT_FILES}
    source_fingerprint = digest(encoded({k: digest(v) for k, v in primary_snapshots.items()}))
    if manifest.source_fingerprint != source_fingerprint:
        raise ValueError("step 6 captured source fingerprint mismatch")
    for name in (*STEP6_INPUT_FILES, "annotations.jsonl"):
        key = name if name == "annotations.jsonl" else f"input/{name}"
        reference = manifest.files.get(key)
        data = snapshots[name]
        if (
            reference is None
            or reference.size_bytes != len(data)
            or reference.sha256 != digest(data)
        ):
            raise ValueError("step 6 captured source hash mismatch")
    primary_artifacts, _ = build_step6(primary_snapshots)
    if set(manifest.files) != set(primary_artifacts):
        raise ValueError("step 6 captured manifest file set mismatch")
    for name, expected in primary_artifacts.items():
        reference = manifest.files[name]
        if reference.sha256 != digest(expected) or reference.size_bytes != len(expected):
            raise ValueError("step 6 captured manifest does not match replay")
    if primary_artifacts["annotations.jsonl"] != snapshots["annotations.jsonl"]:
        raise ValueError("step 6 annotations do not match captured inputs")
    annotations = [
        Annotation.model_validate(unique_json(line))
        for line in snapshots["annotations.jsonl"].splitlines()
    ]
    source_lines = snapshots["answers.jsonl"].split(b"\n")
    originals = {
        a.answer_id: Answer.model_validate(unique_json(source_lines[a.source_line - 1]))
        for a in annotations
    }
    prompts = PromptSet.model_validate(unique_json(snapshots["prompts.json"]))
    target = Target.model_validate(unique_json(snapshots["target.json"]))
    policy = ReviewPolicy.model_validate(unique_json(snapshots["policy.json"]))
    context_fingerprint = digest(encoded({name: digest(snapshots[name]) for name in SOURCE_FILES}))
    selected, jobs, eligible = select_jobs(
        annotations, originals, prompts, target, policy, context_fingerprint
    )
    reviews, intake = accept_results(snapshots["review-results.jsonl"], jobs)
    effective, resolved_fields = resolve(annotations, selected, reviews)
    metrics = agreements(annotations, selected, reviews, policy)
    successful = [r for r in reviews.values() if r.status == "ok"]
    model_count = sum(r.reviewer.kind == "model" for r in successful)
    manual_count = sum(r.reviewer.kind == "manual" for r in successful)
    real_count = model_count + manual_count
    quarantined = sum(r["outcome"] == "quarantined" for r in intake)
    errors = sum(r.status == "error" for r in reviews.values())
    model_reviewer = next(
        (
            r.reviewer
            for r in sorted(successful, key=lambda r: r.job_id)
            if r.reviewer.kind == "model"
        ),
        None,
    )
    status: Literal["complete", "pending_api_key", "partial"] = (
        "partial"
        if quarantined or errors
        else "complete"
        if not jobs or real_count == len(jobs)
        else "pending_api_key"
        if not snapshots["review-results.jsonl"].strip()
        else "partial"
    )
    summary = Step7Summary(
        status=status,
        eligible_answers=eligible,
        synthetic_input_answers=sum(a.origin == "synthetic" for a in annotations),
        random_sample_count=sum(s.random_sample for s in selected),
        uncertain_count=sum(s.uncertain_case for s in selected),
        overlap_count=sum(s.random_sample and s.uncertain_case for s in selected),
        selected_count=len(selected),
        accepted_results=len(reviews),
        quarantined_rows=quarantined,
        duplicate_rows=sum(r["outcome"] == "duplicate" for r in intake),
        model_reviews=model_count,
        manual_reviews=manual_count,
        synthetic_reviews=sum(r.reviewer.kind == "synthetic" for r in successful),
        error_results=errors,
        pending_jobs=len(jobs) - real_count,
        resolved_fields=resolved_fields,
        unresolved_answers=sum(
            bool(row["outstanding_fields"]) and bool(row["measurement_eligible"])
            for row in effective
        ),
        conflicting_answers=sum(
            bool(row["confident_disagreements"]) or bool(row["brand_disagreements"])
            for row in effective
        ),
        rank_outside_scope_answers=sum(has_rank_gap(row) for row in effective),
        model_reviewer=model_reviewer,
        independent_review="imported_declarations_only" if real_count else "not_performed",
        metrics=metrics,
    )
    artifacts = {f"input/{name}": data for name, data in snapshots.items()}
    artifacts["selection.json"] = encoded(
        {
            "schema_version": "step7-selection-1.1",
            "policy": policy.model_dump(mode="json"),
            "selected": [s.model_dump(mode="json") for s in selected],
            "warning": "Internal file; do not send selection reasons or primary "
            "decisions to reviewer.",
        }
    )
    artifacts["blind_jobs.jsonl"] = b"".join(encoded(job.model_dump(mode="json")) for job in jobs)
    artifacts["crosscheck.jsonl"] = b"".join(
        encoded(reviews[job_id].model_dump(mode="json")) for job_id in sorted(reviews)
    )
    artifacts["review_intake.jsonl"] = b"".join(encoded(row) for row in intake)
    artifacts["quarantine.jsonl"] = b"".join(
        encoded(row) for row in intake if row["outcome"] == "quarantined"
    )
    artifacts["resolved_annotations.jsonl"] = b"".join(encoded(row) for row in effective)
    artifacts["agreement.json"] = encoded(
        {name: value.model_dump(mode="json") for name, value in metrics.items()}
    )
    artifacts["quality.json"] = encoded(
        {
            "schema_version": "step7-quality-1.1",
            "unresolved_answers": summary.unresolved_answers,
            "conflicting_answers": summary.conflicting_answers,
            "rank_outside_scope_answers": summary.rank_outside_scope_answers,
            "quarantine_reasons": {
                reason: sum(row["reason"] == reason for row in intake)
                for reason in sorted(
                    {str(row["reason"]) for row in intake if row["outcome"] == "quarantined"}
                )
            },
            "model_reviewer": model_reviewer.model_dump(mode="json") if model_reviewer else None,
            "declared_imports_only": True,
            "model_execution_observed": False,
        }
    )
    artifacts["summary.json"] = encoded(summary.model_dump(mode="json"))
    artifacts["executor.json"] = encoded(
        {
            "status": "disabled",
            "executed": False,
            "network_requests": 0,
            "model_calls": 0,
            "imported_provenance": "declared_by_input_file_not_observed_by_this_program",
            "primary_method": "rules",
            "primary_model_family": None,
        }
    )
    artifacts["validation.json"] = encoded(
        {
            "schema_version": "step7-validation-1.0",
            "source_replay_valid": True,
            "blind_payloads_contain_no_primary_decisions": True,
            "random_sample_never_rewrites_confident_fields": True,
            "imported_quotes_verified": True,
        }
    )
    artifacts["report.html"] = render_report(
        summary, selected, jobs, policy, effective=effective, annotations=annotations, intake=intake
    )
    if sum(map(len, artifacts.values())) > MAX_TOTAL_BYTES or any(
        len(data) > MAX_ANSWERS_BYTES for data in artifacts.values()
    ):
        raise ValueError("step 7 publication exceeds byte budget")
    return artifacts, summary


def run_step7(
    step6_dir: Path,
    output_dir: Path,
    *,
    policy: ReviewPolicy | None = None,
    results_file: Path | None = None,
) -> Step7Summary:
    source, output = EvidenceStore(step6_dir), EvidenceStore(output_dir)
    if (
        source.root == output.root
        or source.root in output.root.parents
        or output.root in source.root.parents
    ):
        raise ValueError("source and destination must be separate")
    verify_step6(source.root)
    snapshots = {
        name: source.read_bytes(
            f"input/{name}",
            limit=(
                MAX_METADATA_BYTES
                if name in {"prompts.json", "target.json", "step5-manifest.json"}
                else MAX_ANSWERS_BYTES
            ),
        )
        for name in STEP6_INPUT_FILES
    }
    snapshots["step6-manifest.json"] = source.read_bytes("manifest.json", limit=MAX_METADATA_BYTES)
    snapshots["annotations.jsonl"] = source.read_bytes("annotations.jsonl", limit=MAX_ANSWERS_BYTES)
    snapshots["policy.json"] = encoded((policy or ReviewPolicy()).model_dump(mode="json"))
    snapshots["review-results.jsonl"] = (
        read_file(results_file, MAX_ANSWERS_BYTES) if results_file else b""
    )
    artifacts, summary = build_artifacts(snapshots)
    output.prepare(new=True)
    with pinned_directory(output.root):
        with pinned_directory(output.root / "input", create=True):
            pass
        for name, data in artifacts.items():
            output.atomic_bytes(name, data)
        output.atomic_json(
            "manifest.json",
            PublicationManifest(
                source_fingerprint=digest(encoded({k: digest(v) for k, v in snapshots.items()})),
                files={
                    k: FileDigest(sha256=digest(v), size_bytes=len(v)) for k, v in artifacts.items()
                },
            ).model_dump(mode="json"),
        )
    return summary


def verify_publication(directory: Path) -> Step7Summary:
    source = EvidenceStore(directory)
    manifest = PublicationManifest.model_validate(
        unique_json(source.read_bytes("manifest.json", limit=MAX_METADATA_BYTES))
    )
    snapshots = {
        name: source.read_bytes(
            f"input/{name}",
            limit=(
                MAX_METADATA_BYTES
                if name
                in {
                    "prompts.json",
                    "target.json",
                    "step5-manifest.json",
                    "step6-manifest.json",
                    "policy.json",
                }
                else MAX_ANSWERS_BYTES
            ),
        )
        for name in INPUT_FILES
    }
    artifacts, summary = build_artifacts(snapshots)
    fingerprint = digest(encoded({k: digest(v) for k, v in snapshots.items()}))
    if set(artifacts) != set(manifest.files) or manifest.source_fingerprint != fingerprint:
        raise ValueError("step 7 manifest mismatch")
    for name, data in artifacts.items():
        ref = manifest.files[name]
        if (
            ref.sha256 != digest(data)
            or ref.size_bytes != len(data)
            or source.read_bytes(name, limit=len(data)) != data
        ):
            raise ValueError("step 7 publication does not match replay")
    return summary
