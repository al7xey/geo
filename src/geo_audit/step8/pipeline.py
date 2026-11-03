"""Replay-verified, bounded offline normalization of the step 7 handoff."""

from pathlib import Path

from ..evidence import MAX_TOTAL_BYTES, EvidenceStore
from ..secure_io import pinned_directory
from ..step5.models import MechanicalAnswer, PublicationFile, Target
from ..step5.pipeline import MAX_ANSWERS_BYTES, MAX_METADATA_BYTES, digest, encoded, unique_json
from ..step6.models import Annotation
from ..step7.models import PublicationManifest as Step7Manifest
from ..step7.models import ResolvedAnnotation
from ..step7.pipeline import INPUT_FILES as STEP7_INPUT_FILES
from ..step7.pipeline import build_artifacts as build_step7
from ..step7.pipeline import verify_publication as verify_step7
from .grouping import group_brands, group_domains, pending_jobs
from .models import EntityCatalog, PublicationManifest, Step8Policy, Step8Summary
from .report import render_report

SOURCE_FILES = (
    *STEP7_INPUT_FILES,
    "step7-manifest.json",
    "resolved_annotations.jsonl",
    "crosscheck.jsonl",
)
INPUT_FILES = (*SOURCE_FILES, "entity-catalog.json", "entity-policy.json")


def build_artifacts(snapshots: dict[str, bytes]) -> tuple[dict[str, bytes], Step8Summary]:
    if set(snapshots) != set(INPUT_FILES):
        raise ValueError("unexpected step 8 input set")
    if sum(map(len, snapshots.values())) > MAX_TOTAL_BYTES or any(
        len(data) > (MAX_METADATA_BYTES if name.endswith(".json") else MAX_ANSWERS_BYTES)
        for name, data in snapshots.items()
    ):
        raise ValueError("step 8 input exceeds byte budget")
    manifest = Step7Manifest.model_validate(unique_json(snapshots["step7-manifest.json"]))
    prior = {name: snapshots[name] for name in STEP7_INPUT_FILES}
    fingerprint = digest(encoded({name: digest(data) for name, data in prior.items()}))
    if manifest.source_fingerprint != fingerprint:
        raise ValueError("step 7 captured source fingerprint mismatch")
    replay, _ = build_step7(prior)
    if set(replay) != set(manifest.files):
        raise ValueError("step 7 manifest file set mismatch")
    for name, expected in replay.items():
        reference = manifest.files[name]
        if reference.sha256 != digest(expected) or reference.size_bytes != len(expected):
            raise ValueError("step 7 captured manifest does not match replay")
    for name in ("resolved_annotations.jsonl", "crosscheck.jsonl"):
        if snapshots[name] != replay[name]:
            raise ValueError("step 7 effective results do not match captured inputs")
    annotations = [
        Annotation.model_validate(unique_json(line))
        for line in snapshots["annotations.jsonl"].splitlines()
    ]
    effective = [
        ResolvedAnnotation.model_validate(unique_json(line))
        for line in snapshots["resolved_annotations.jsonl"].splitlines()
    ]
    mechanical = [
        MechanicalAnswer.model_validate(unique_json(line))
        for line in snapshots["mechanical.jsonl"].splitlines()
    ]
    target = Target.model_validate(unique_json(snapshots["target.json"]))
    catalog = EntityCatalog.model_validate(unique_json(snapshots["entity-catalog.json"]))
    policy = Step8Policy.model_validate(unique_json(snapshots["entity-policy.json"]))
    occurrences, entities = group_brands(annotations, effective, target, catalog, policy)
    domains = group_domains(mechanical, target, catalog)
    jobs, evaluations, complete, deferred = pending_jobs(entities, occurrences, domains, policy)
    unknown = sum(d.kind == "unknown" for d in domains)
    unresolved = sum(e.identity_scope == "unresolved" for e in entities)
    occurrence_ids = {o.occurrence_id for o in occurrences}
    grouped_ids = [oid for e in entities for oid in e.occurrence_ids]
    covered_brands = {(o.answer_id, o.primary_brand_index) for o in occurrences}
    expected_brands = {(a.answer_id, i) for a in annotations for i in range(len(a.brands))}
    if (
        len(occurrence_ids) != len(occurrences)
        or len(grouped_ids) != len(occurrence_ids)
        or set(grouped_ids) != occurrence_ids
        or covered_brands != expected_brands
    ):
        raise ValueError("entity partition coverage mismatch")
    summary = Step8Summary(
        status="partial"
        if unknown
        or unresolved
        or jobs
        or not complete
        or deferred
        or not any(a.measurement_eligible for a in annotations)
        else "complete",
        source_answers=len(annotations),
        occurrences=len(occurrences),
        entity_groups=len(entities),
        non_target_groups=sum(e.identity_scope == "non_target_candidate" for e in entities),
        unresolved_identity_groups=unresolved,
        source_hosts=len(domains),
        unknown_source_types=unknown,
        pair_jobs=sum(j.task_type == "compare_entity_names" for j in jobs),
        domain_jobs=sum(j.task_type == "classify_source_host" for j in jobs),
        pair_evaluations=evaluations,
        pair_search_complete=complete,
        domains_deferred=deferred,
    )
    artifacts = {f"input/{name}": data for name, data in snapshots.items()}
    artifacts["brand_map.json"] = encoded(
        {
            "schema_version": "step8-brand-map-1.0",
            "entities": [e.model_dump(mode="json") for e in entities],
        }
    )
    artifacts["brand_occurrences.jsonl"] = b"".join(
        encoded(o.model_dump(mode="json")) for o in occurrences
    )
    artifacts["domain_types.json"] = encoded(
        {
            "schema_version": "step8-domain-types-1.0",
            "domains": [d.model_dump(mode="json") for d in domains],
        }
    )
    artifacts["unresolved_entities.jsonl"] = b"".join(
        encoded(j.model_dump(mode="json")) for j in jobs
    )
    artifacts["summary.json"] = encoded(summary.model_dump(mode="json"))
    artifacts["quality.json"] = encoded(
        {
            "schema_version": "step8-quality-1.0",
            "identity_pending_step7_groups": unresolved,
            "unknown_source_types": unknown,
            "pair_search_complete_within_prefix_buckets": complete,
            "candidate_search_is_not_exhaustive": True,
            "pair_evaluations": evaluations,
            "domains_deferred": deferred,
            "synthetic_input_answers": sum(a.origin == "synthetic" for a in annotations),
            "source_semantics_pending": sum(bool(e.outstanding_fields) for e in effective),
            "source_conflicts": sum(
                bool(e.confident_disagreements or e.brand_disagreements) for e in effective
            ),
            "non_target_names_are_candidates_only": True,
        }
    )
    artifacts["executor.json"] = encoded(
        {
            "status": "disabled",
            "executed": False,
            "model_calls": 0,
            "network_requests": 0,
            "import_supported": "explicit_local_catalog_only",
        }
    )
    artifacts["validation.json"] = encoded(
        {
            "source_replay_valid": True,
            "all_primary_brands_covered": True,
            "occurrences_partitioned_once": True,
            "unresolved_identity_never_merged_with_target": True,
            "external_types_require_catalog": True,
        }
    )
    artifacts["report.html"] = render_report(summary, entities, domains, jobs)
    if sum(map(len, artifacts.values())) > MAX_TOTAL_BYTES or any(
        len(data) > MAX_ANSWERS_BYTES for data in artifacts.values()
    ):
        raise ValueError("step 8 publication exceeds byte budget")
    return artifacts, summary


def capture_inputs(source: EvidenceStore, *, published: bool) -> dict[str, bytes]:
    """Read only fixed paths and enforce the cumulative budget while capturing."""
    snapshots: dict[str, bytes] = {}
    total = 0
    for name in INPUT_FILES if published else SOURCE_FILES:
        path = (
            f"input/{name}"
            if published or name in STEP7_INPUT_FILES
            else ("manifest.json" if name == "step7-manifest.json" else name)
        )
        metadata = name.endswith(".json")
        limit = min(MAX_METADATA_BYTES if metadata else MAX_ANSWERS_BYTES, MAX_TOTAL_BYTES - total)
        data = source.read_bytes(path, limit=limit)
        total += len(data)
        snapshots[name] = data
    return snapshots


def run_step8(
    step7_dir: Path,
    output_dir: Path,
    *,
    catalog: EntityCatalog | None = None,
    policy: Step8Policy | None = None,
) -> Step8Summary:
    source, output = EvidenceStore(step7_dir), EvidenceStore(output_dir)
    if (
        source.root == output.root
        or source.root in output.root.parents
        or output.root in source.root.parents
    ):
        raise ValueError("source and destination must be separate")
    verify_step7(source.root)
    snapshots = capture_inputs(source, published=False)
    snapshots["entity-catalog.json"] = encoded((catalog or EntityCatalog()).model_dump(mode="json"))
    snapshots["entity-policy.json"] = encoded((policy or Step8Policy()).model_dump(mode="json"))
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
                    k: PublicationFile(sha256=digest(v), size_bytes=len(v))
                    for k, v in artifacts.items()
                },
            ).model_dump(mode="json"),
        )
    return summary


def verify_publication(directory: Path) -> Step8Summary:
    source = EvidenceStore(directory)
    manifest = PublicationManifest.model_validate(
        unique_json(source.read_bytes("manifest.json", limit=MAX_METADATA_BYTES))
    )
    snapshots = capture_inputs(source, published=True)
    artifacts, summary = build_artifacts(snapshots)
    fingerprint = digest(encoded({k: digest(v) for k, v in snapshots.items()}))
    if set(artifacts) != set(manifest.files) or manifest.source_fingerprint != fingerprint:
        raise ValueError("step 8 manifest mismatch")
    for name, data in artifacts.items():
        ref = manifest.files[name]
        if (
            ref.sha256 != digest(data)
            or ref.size_bytes != len(data)
            or source.read_bytes(name, limit=len(data)) != data
        ):
            raise ValueError("step 8 publication does not match replay")
    return summary
