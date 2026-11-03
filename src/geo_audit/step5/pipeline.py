"""Bounded offline intake, no-loss accounting and new-only committed publication."""

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..evidence import MAX_TOTAL_BYTES, EvidenceStore
from ..psl import PSL_SHA256, load_bundled_psl
from ..secure_io import pinned_directory
from .extract import Extractor
from .models import (
    Answer,
    IntakeRecord,
    MechanicalAnswer,
    ObservedFact,
    Prompt,
    PromptSet,
    PublicationFile,
    PublicationManifest,
    Step5Summary,
    Target,
)
from .report import render_report

MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_ANSWERS_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
MAX_ROWS = 10000
MAX_TOTAL_OBSERVATIONS = 50000
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n").encode()


def unique_json(data: bytes) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value: str) -> object:
        raise ValueError("non-finite JSON number")

    return json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)


@dataclass
class Row:
    line: int
    sha256: str
    identity: str | None
    answer: Answer | None
    fingerprint: str
    reason: str | None


def parse_rows(data: bytes) -> list[Row]:
    rows = []
    if len(data) > MAX_ANSWERS_BYTES:
        raise ValueError("answers exceed byte budget")
    row_count = data.count(b"\n") + int(bool(data) and not data.endswith(b"\n"))
    if row_count > MAX_ROWS:
        raise ValueError("answers exceed row budget")
    # Split only JSONL LF, not Unicode line separators inside JSON strings.
    lines = data.split(b"\n")
    if lines and not lines[-1]:
        lines.pop()
    for number, line in enumerate(lines, 1):
        identity = None
        answer = None
        fingerprint = digest(line)
        reason = None
        try:
            if len(line) > MAX_LINE_BYTES:
                raise ValueError("line exceeds size budget")
            value = unique_json(line)
            if isinstance(value, dict):
                raw_id = value.get("answer_id")
                if isinstance(raw_id, str) and ID.fullmatch(raw_id):
                    identity = raw_id
            fingerprint = digest(encoded(value))
            answer = Answer.model_validate(value)
        except (ValueError, RecursionError):
            reason = "invalid_answer_or_json"
        rows.append(Row(number, digest(line), identity, answer, fingerprint, reason))
    return rows


def slot_key(answer: Answer) -> bytes:
    return encoded(
        [
            answer.prompt_set_id,
            answer.prompt_id,
            answer.provider,
            answer.model,
            answer.repeat_index,
            answer.origin,
            answer.search_mode,
            answer.country,
            answer.language,
            answer.settings,
        ]
    )


def answer_reason(answer: Answer, prompt_map: dict[str, Prompt], prompt_set_id: str) -> str | None:
    prompt = prompt_map.get(answer.prompt_id)
    if answer.prompt_set_id != prompt_set_id:
        return "prompt_set_mismatch"
    if prompt is None:
        return "unknown_prompt"
    if not prompt.active:
        return "inactive_prompt"
    if answer.group is not None and answer.group != prompt.group:
        return "group_conflict"
    if answer.status == "ok" and not answer.text.strip():
        return "ok_answer_is_empty"
    if answer.status == "empty" and answer.text.strip():
        return "empty_answer_has_text"
    if answer.text_sha256 is not None and answer.text_sha256 != digest(answer.text.encode()):
        return "text_hash_mismatch"
    if answer.synthetic != (answer.origin == "synthetic"):
        return "origin_flag_conflict"
    return None


def process(
    prompts: PromptSet,
    target: Target,
    data: bytes,
) -> tuple[list[MechanicalAnswer], list[IntakeRecord], Step5Summary]:
    rows = parse_rows(data)
    prompt_map = {p.prompt_id: p for p in prompts.prompts}
    by_id: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        if row.answer is not None:
            row.reason = answer_reason(row.answer, prompt_map, prompts.prompt_set_id)
        if row.identity is not None:
            by_id[row.identity].append(row)
    # Reject every conflicting occurrence, independent of file ordering.
    id_conflicts = {
        identity for identity, items in by_id.items() if len({row.fingerprint for row in items}) > 1
    }
    by_slot: dict[bytes, set[str]] = defaultdict(set)
    for row in rows:
        if row.answer is not None and row.reason is None and row.identity not in id_conflicts:
            by_slot[slot_key(row.answer)].add(row.answer.answer_id)
    slot_conflicts = {
        identity for values in by_slot.values() if len(values) > 1 for identity in values
    }
    extractor = Extractor(target, load_bundled_psl())
    mechanical: list[MechanicalAnswer] = []
    intake: list[IntakeRecord] = []
    seen: set[str] = set()
    failed: dict[str, str] = {}
    observation_count = 0
    mechanical_bytes = 0
    for row in rows:
        answer = row.answer
        reason = row.reason
        if row.identity in id_conflicts:
            reason = "answer_id_conflict"
        elif row.identity in slot_conflicts:
            reason = "logical_slot_conflict"
        if answer is not None and reason is None:
            prompt = prompt_map[answer.prompt_id]
            if answer.answer_id in failed:
                reason = failed[answer.answer_id]
            elif answer.answer_id in seen:
                intake.append(
                    IntakeRecord(
                        line=row.line,
                        line_sha256=row.sha256,
                        answer_id=answer.answer_id,
                        outcome="duplicate",
                        reason="identical_reimport",
                    )
                )
                continue
            else:
                try:
                    candidates, sources, invalid = extractor.extract(answer)
                except ValueError:
                    reason = "extraction_budget_exceeded"
                    failed[answer.answer_id] = reason
                else:
                    record = MechanicalAnswer(
                        answer_id=answer.answer_id,
                        prompt_id=answer.prompt_id,
                        group=prompt.group,
                        provider=answer.provider,
                        model=answer.model,
                        origin=answer.origin,
                        repeat_index=answer.repeat_index,
                        status=answer.status,
                        source_line=row.line,
                        text_sha256=digest(answer.text.encode()),
                        text_chars=len(answer.text),
                        visibility_eligible=prompt.visibility_eligible
                        and answer.collection_eligible,
                        candidates=candidates,
                        sources=sources,
                        invalid_source_count=invalid,
                        target_name_candidate=any(c.location == "body" for c in candidates),
                        target_domain_in_body=any(
                            s.target_host_match and "text" in s.origins for s in sources
                        ),
                        target_domain_in_citations=any(
                            s.target_host_match and "citation" in s.origins for s in sources
                        ),
                    )
                    observation_count += len(candidates) + len(sources)
                    mechanical_bytes += len(encoded(record.model_dump(mode="json")))
                    if (
                        observation_count > MAX_TOTAL_OBSERVATIONS
                        or mechanical_bytes > MAX_ANSWERS_BYTES
                    ):
                        raise ValueError("mechanical output exceeds shared extraction budget")
                    mechanical.append(record)
                    seen.add(answer.answer_id)
        intake.append(
            IntakeRecord(
                line=row.line,
                line_sha256=row.sha256,
                answer_id=row.identity,
                outcome="quarantined" if reason else "accepted",
                reason=reason,
            )
        )
    groups = {group: sum(a.group == group for a in mechanical) for group in "ABCD"}
    outcomes = Counter(r.outcome for r in intake)
    if len(mechanical) != outcomes["accepted"] or sum(outcomes.values()) != len(rows):
        raise RuntimeError("intake conservation invariant failed")
    summary = Step5Summary(
        status="partial" if outcomes["quarantined"] or not mechanical else "complete",
        input_lines=len(rows),
        accepted=outcomes["accepted"],
        duplicates=outcomes["duplicate"],
        quarantined=outcomes["quarantined"],
        prompt_groups=groups,
        evidence_categories={
            "observed_fact": len(mechanical) + sum(len(a.candidates) for a in mechanical),
            "calculated_estimate": 0,
            "hypothesis": 0,
            "recommendation": 0,
        },
        delivery_statuses=dict(Counter(a.status for a in mechanical)),
        target_name_candidates=sum(a.target_name_candidate for a in mechanical),
        target_citation_host_matches=sum(a.target_domain_in_citations for a in mechanical),
        ambiguous_name_candidates=sum(c.ambiguous for a in mechanical for c in a.candidates),
        invalid_sources=sum(a.invalid_source_count for a in mechanical),
        warnings=(["no_accepted_answers"] if not mechanical else [])
        + (["invalid_sources_skipped"] if any(a.invalid_source_count for a in mechanical) else []),
    )
    return mechanical, intake, summary


def build_artifacts(snapshots: dict[str, bytes]) -> tuple[dict[str, bytes], Step5Summary]:
    """Deterministic build, also used to verify that published claims match input."""
    prompts = PromptSet.model_validate(unique_json(snapshots["prompts.json"]))
    target = Target.model_validate(unique_json(snapshots["target.json"]))
    mechanical, intake, summary = process(prompts, target, snapshots["answers.jsonl"])
    artifacts = {f"input/{name}": value for name, value in snapshots.items()}
    artifacts["mechanical.jsonl"] = b"".join(encoded(a.model_dump(mode="json")) for a in mechanical)
    artifacts["intake.jsonl"] = b"".join(encoded(r.model_dump(mode="json")) for r in intake)
    artifacts["quarantine.jsonl"] = b"".join(
        encoded(r.model_dump(mode="json")) for r in intake if r.outcome == "quarantined"
    )
    for group in "ABCD":
        artifacts[f"prompt_groups/{group}.jsonl"] = b"".join(
            encoded(
                {
                    "answer_id": a.answer_id,
                    "prompt_id": a.prompt_id,
                    "group": a.group,
                    "source_line": a.source_line,
                    "status": a.status,
                    "visibility_eligible": a.visibility_eligible,
                }
            )
            for a in mechanical
            if a.group == group
        )
    facts = []
    for index, answer in enumerate(mechanical, 1):
        facts.append(
            ObservedFact(
                fact_id=f"fact-{index}-record",
                kind="answer_record",
                answer_id=answer.answer_id,
                source_line=answer.source_line,
                text_sha256=answer.text_sha256,
                delivery_status=answer.status,
            )
        )
        for match_index, match in enumerate(answer.candidates, 1):
            facts.append(
                ObservedFact(
                    fact_id=f"fact-{index}-match-{match_index}",
                    kind="name_match",
                    answer_id=answer.answer_id,
                    source_line=answer.source_line,
                    text_sha256=answer.text_sha256,
                    quote=match.quote,
                    start=match.start,
                    end=match.end,
                    ambiguous_identity=match.ambiguous,
                    location=match.location,
                    delivery_status=answer.status,
                )
            )
    artifacts["categories/observed_fact.jsonl"] = b"".join(
        encoded(fact.model_dump(mode="json")) for fact in facts
    )
    category_status: dict[str, object] = {
        "observed_fact": {"status": "available", "count": len(facts)},
    }
    for category, later_step in (
        ("calculated_estimate", 9),
        ("hypothesis", 11),
        ("recommendation", 11),
    ):
        artifacts[f"categories/{category}.jsonl"] = b""
        category_status[category] = {
            "status": "not_produced_at_step5",
            "count": 0,
            "planned_step": later_step,
        }
    artifacts["categories/index.json"] = encoded(
        {
            "schema_version": "evidence-categories-1.0",
            "categories": category_status,
            "scope": "Provenance of audit findings, not truth of statements made by a model.",
        }
    )
    artifacts["summary.json"] = encoded(summary.model_dump(mode="json"))
    original_lines = snapshots["answers.jsonl"].split(b"\n")
    originals = {
        a.answer_id: Answer.model_validate(unique_json(original_lines[a.source_line - 1]))
        for a in mechanical
    }
    artifacts["report.html"] = render_report(
        prompts, target, mechanical, intake, summary, originals
    )
    artifacts["validation.json"] = encoded(
        {
            "schema_version": "step5-validation-1.0",
            "accounting_valid": True,
            "one_group_per_accepted_answer": True,
            "source_text_preserved": True,
            "network_performed": False,
            "roles_assessed": False,
        }
    )
    if sum(map(len, artifacts.values())) > MAX_TOTAL_BYTES:
        raise ValueError("publication exceeds total byte budget")
    if any(len(value) > MAX_ANSWERS_BYTES for value in artifacts.values()):
        raise ValueError("publication artifact exceeds byte budget")
    return artifacts, summary


def run_step5(input_dir: Path, output_dir: Path) -> Step5Summary:
    """Build a new publication; manifest.json is written last, never read raw_ref."""
    source = EvidenceStore(input_dir)
    output = EvidenceStore(output_dir)
    if (
        source.root == output.root
        or source.root in output.root.parents
        or output.root in source.root.parents
    ):
        raise ValueError("input and output directories must be separate")
    snapshots = {
        "prompts.json": source.read_bytes("prompts.json", limit=MAX_METADATA_BYTES),
        "target.json": source.read_bytes("target.json", limit=MAX_METADATA_BYTES),
        "answers.jsonl": source.read_bytes("answers.jsonl", limit=MAX_ANSWERS_BYTES),
    }
    artifacts, summary = build_artifacts(snapshots)
    # Do all fatal validation before creating a publication directory.
    output.prepare(new=True)
    with pinned_directory(output.root):
        for name in ("input", "prompt_groups", "categories"):
            with pinned_directory(output.root / name, create=True):
                pass
        for name, value in artifacts.items():
            output.atomic_bytes(name, value)
        output.atomic_json(
            "manifest.json",
            PublicationManifest(
                psl_sha256=PSL_SHA256,
                input_fingerprint=digest(encoded({k: digest(v) for k, v in snapshots.items()})),
                files={
                    k: PublicationFile(sha256=digest(v), size_bytes=len(v))
                    for k, v in artifacts.items()
                },
            ).model_dump(mode="json"),
        )
    return summary


def verify_publication(directory: Path) -> Step5Summary:
    """Verify a committed current-version result, including replay from source snapshots.

    Hashes check accidental corruption, replay checks consistency, neither proves that
    input answers really originated from a provider. No manifest-supplied path is opened.
    """
    source = EvidenceStore(directory)
    manifest = PublicationManifest.model_validate(
        unique_json(source.read_bytes("manifest.json", limit=MAX_METADATA_BYTES))
    )
    snapshots = {
        "prompts.json": source.read_bytes("input/prompts.json", limit=MAX_METADATA_BYTES),
        "target.json": source.read_bytes("input/target.json", limit=MAX_METADATA_BYTES),
        "answers.jsonl": source.read_bytes("input/answers.jsonl", limit=MAX_ANSWERS_BYTES),
    }
    artifacts, summary = build_artifacts(snapshots)
    if (
        set(manifest.files) != set(artifacts)
        or manifest.psl_sha256 != PSL_SHA256
        or manifest.input_fingerprint
        != digest(encoded({k: digest(v) for k, v in snapshots.items()}))
    ):
        raise ValueError("publication manifest mismatch")
    for name, expected in artifacts.items():
        reference = manifest.files[name]
        if reference.size_bytes != len(expected) or reference.sha256 != digest(expected):
            raise ValueError("publication does not match source replay")
        actual = source.read_bytes(name, limit=len(expected))
        if actual != expected:
            raise ValueError("publication artifact corrupted")
    return summary
