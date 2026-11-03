"""Read-only adapter for the supplied step 3/4 export; never opens raw API files."""

import json
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..contracts import SHA256, Contract, Identifier
from ..evidence import MAX_TOTAL_BYTES, EvidenceStore
from ..secure_io import pinned_directory
from ..urls import normalize_host
from .models import Answer, Citation, Group, Prompt, PromptSet, PublicationFile, ShortText, Target
from .pipeline import (
    MAX_ANSWERS_BYTES,
    MAX_LINE_BYTES,
    MAX_METADATA_BYTES,
    MAX_ROWS,
    digest,
    encoded,
    unique_json,
)


class UpstreamData(BaseModel):
    # Extra collector accounting fields are retained verbatim in source snapshots.
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)


class UpstreamQuestion(UpstreamData):
    id: Identifier
    text: str = Field(min_length=1, max_length=10000)
    level: Literal[1, 2, 3, 4]
    origin: Literal["suggested", "edited", "custom"]
    has_brand: bool
    counts_in_organic_visibility: bool

    @field_validator("level", mode="before")
    @classmethod
    def integer_level(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("question level must be an integer")
        return value

    @model_validator(mode="after")
    def organic(self) -> "UpstreamQuestion":
        if self.has_brand == self.counts_in_organic_visibility or not self.text.strip():
            raise ValueError("inconsistent approved question")
        return self


class Step4Input(UpstreamData):
    schema_version: Literal["step4-input-1"]
    domain: ShortText
    language: ShortText
    country: ShortText
    name_variants: list[ShortText] = Field(min_length=1, max_length=100)
    prompts: list[UpstreamQuestion] = Field(min_length=1, max_length=50)
    prompts_sha256: SHA256


class NameVariant(UpstreamData):
    variant: ShortText
    ambiguous: bool = True  # Missing ambiguity metadata must never imply unique identity.


class ApprovedProfile(UpstreamData):
    domain: ShortText
    name_variants: list[NameVariant] = Field(min_length=1, max_length=100)


class CollectorAnswer(UpstreamData):
    answer_id: Identifier
    prompt_id: Identifier
    system: ShortText
    model: ShortText
    effort: ShortText | None = None
    repeat: Annotated[int, Field(ge=1, le=10000)]
    status: Literal["ok", "empty", "error", "skipped"]
    text: str = Field(default="", max_length=500000)
    sources: list[Annotated[str, Field(min_length=1, max_length=4096)]] = Field(
        default_factory=list, max_length=200
    )
    created_at: str | None = Field(default=None, max_length=100)
    raw_file: str | None = Field(default=None, max_length=1000)
    raw_sha256: SHA256 | None = None


class AdapterPolicy(Contract):
    synthetic: bool = False


class AdapterManifest(Contract):
    schema_version: Literal["step4-to-step5-1.0"] = "step4-to-step5-1.0"
    committed: Literal[True] = True
    source_fingerprint: SHA256
    files: dict[str, PublicationFile] = Field(min_length=1, max_length=12)


INPUT_FILES = ("step4-input.json", "profile.json", "answers.jsonl", "policy.json")
GROUPS: dict[int, Group] = {1: "A", 2: "B", 3: "C", 4: "D"}


def validate_approval(
    step4_input: bytes, profile_input: bytes
) -> tuple[Step4Input, ApprovedProfile]:
    if max(len(step4_input), len(profile_input)) > MAX_METADATA_BYTES:
        raise ValueError("approved metadata budget exceeded")
    raw = unique_json(step4_input)
    if not isinstance(raw, dict):
        raise ValueError("step 4 input must be an object")
    approved = Step4Input.model_validate(raw)
    if any(
        not v.strip()
        for v in (approved.domain, approved.language, approved.country, *approved.name_variants)
    ):
        raise ValueError("blank approved metadata")
    # Exactly the canonical checksum used by the unchanged tools/step4_handoff.py.
    checksum = digest(
        json.dumps(
            raw.get("prompts"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )
    if checksum != approved.prompts_sha256:
        raise ValueError("questions changed after step 3 approval")
    profile = ApprovedProfile.model_validate(unique_json(profile_input))
    names = list(dict.fromkeys(approved.name_variants))
    if set(names) != {n.variant for n in profile.name_variants}:
        raise ValueError("approved names and profile disagree; use the saved step 3 profile")
    host = normalize_host(approved.domain).removeprefix("www.")
    if normalize_host(profile.domain).removeprefix("www.") != host:
        raise ValueError("approved profile belongs to another domain")
    questions = {q.id: q for q in approved.prompts}
    if (
        len(questions) != len(approved.prompts)
        or sum(q.origin == "custom" for q in approved.prompts) > 10
    ):
        raise ValueError("duplicate question IDs or too many custom questions")
    return approved, profile


def build_artifacts(snapshots: dict[str, bytes]) -> dict[str, bytes]:
    if set(snapshots) != set(INPUT_FILES):
        raise ValueError("unexpected adapter input files")
    if sum(map(len, snapshots.values())) > MAX_TOTAL_BYTES or any(
        len(data) > (MAX_ANSWERS_BYTES if name == "answers.jsonl" else MAX_METADATA_BYTES)
        for name, data in snapshots.items()
    ):
        raise ValueError("adapter input budget exceeded")
    approved, profile = validate_approval(snapshots["step4-input.json"], snapshots["profile.json"])
    policy = AdapterPolicy.model_validate(unique_json(snapshots["policy.json"]))
    names = list(dict.fromkeys(approved.name_variants))
    host = normalize_host(approved.domain).removeprefix("www.")
    questions = {q.id: q for q in approved.prompts}
    set_id = "collected-" + digest(snapshots["step4-input.json"])[:24]
    prompts = PromptSet(
        schema_version="1.0",
        prompt_set_id=set_id,
        country=approved.country,
        language=approved.language,
        synthetic=policy.synthetic,
        prompts=[
            Prompt(
                prompt_id=q.id,
                group=GROUPS[q.level],
                text=q.text,
                visibility_eligible=q.counts_in_organic_visibility,
            )
            for q in approved.prompts
        ],
    )
    target = Target(
        target_id="target-" + digest(host.encode())[:24],
        name=names[0],
        aliases=names,
        ambiguous_aliases=[
            n for n in names if any(v.variant == n and v.ambiguous for v in profile.name_variants)
        ],
        domains=[host],
        synthetic=policy.synthetic,
    )
    lines = snapshots["answers.jsonl"].split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    if not lines or len(lines) > MAX_ROWS:
        raise ValueError("empty collection or row budget exceeded")
    latest: dict[tuple[str, str, int], tuple[int, CollectorAnswer]] = {}
    journal = []
    ids: dict[str, tuple[str, str, int]] = {}
    for number, line in enumerate(lines, 1):
        if len(line) > MAX_LINE_BYTES:
            raise ValueError(f"collector line {number}: size budget exceeded")
        try:
            row = CollectorAnswer.model_validate(unique_json(line))
            if row.prompt_id not in questions or (row.status == "ok" and not row.text.strip()):
                raise ValueError("unknown question or empty successful answer")
            slot = (row.prompt_id, row.system, row.repeat)
            if row.answer_id in ids and ids[row.answer_id] != slot:
                raise ValueError("answer ID reused for another collection slot")
            ids[row.answer_id] = slot
        except (ValueError, RecursionError):
            raise ValueError(f"collector line {number}: invalid record") from None
        previous = latest.get(slot)
        journal.append(
            {
                "source_line": number,
                "source_sha256": digest(line),
                "source_answer_id": row.answer_id,
                "slot": list(slot),
                "replaces_line": previous[0] if previous else None,
            }
        )
        # Upstream's documented latest(): the LAST row of each slot replaces earlier attempts.
        latest[slot] = (number, row)
    systems = sorted({r.system for _, r in latest.values()})
    counts = {s: Counter(r.status for _, r in latest.values() if r.system == s) for s in systems}
    excluded = []
    for system, states in counts.items():
        completed = sum(v for key, v in states.items() if key != "skipped")
        failed = states["error"] + states["empty"]
        # Integer comparison preserves the strict >20% upstream rule without rounding.
        if not completed or failed * 5 > completed:
            excluded.append(system)
    answers = []
    links = []
    for slot, (number, row) in sorted(latest.items()):
        answer_id = "answer-" + digest(encoded([set_id, *slot]))[:32]
        answer = Answer(
            answer_id=answer_id,
            prompt_id=row.prompt_id,
            prompt_set_id=set_id,
            group=GROUPS[questions[row.prompt_id].level],
            provider=row.system,
            model=row.model,
            origin="synthetic" if policy.synthetic else "api",
            synthetic=policy.synthetic,
            repeat_index=row.repeat,
            collected_at=row.created_at,
            collection_eligible=row.system not in excluded,
            search_mode="step4_collector",
            country=approved.country,
            language=approved.language,
            settings={"effort": row.effort},
            status="error" if row.status == "skipped" else row.status,
            text=row.text,
            sources=[Citation(url=url, origin="step4_collector") for url in row.sources],
            raw_ref=row.raw_file,
            error={"code": "budget_skipped" if row.status == "skipped" else "collector_error"}
            if row.status in {"skipped", "error"}
            else None,
        )
        answers.append(answer)
        links.append(
            {
                "answer_id": answer_id,
                "source_answer_id": row.answer_id,
                "source_line": number,
                "raw_sha256": row.raw_sha256,
                "slot": list(slot),
            }
        )
    artifacts = {f"source/{name}": data for name, data in snapshots.items()}
    artifacts["prompts.json"] = encoded(prompts.model_dump(mode="json"))
    artifacts["target.json"] = encoded(target.model_dump(mode="json"))
    artifacts["answers.jsonl"] = b"".join(encoded(a.model_dump(mode="json")) for a in answers)
    artifacts["source_links.jsonl"] = b"".join(encoded(row) for row in links)
    artifacts["attempts.jsonl"] = b"".join(encoded(row) for row in journal)
    artifacts["summary.json"] = encoded(
        {
            "schema_version": "step4-to-step5-summary-1.0",
            "source_rows": len(lines),
            "selected_answers": len(answers),
            "replaced_attempts": len(lines) - len(answers),
            "questions": len(questions),
            "organic_questions": sum(q.counts_in_organic_visibility for q in questions.values()),
            "branded_questions": sum(q.has_brand for q in questions.values()),
            "question_origins": dict(Counter(q.origin for q in questions.values())),
            "uncollected_question_ids": sorted(set(questions) - {a.prompt_id for a in answers}),
            "excluded_systems": excluded,
            "delivery_by_system": {s: dict(c) for s, c in counts.items()},
            "synthetic": policy.synthetic,
            "raw_files_opened": 0,
            "network_requests": 0,
            "model_calls": 0,
            "provenance": "declared_by_collector_not_independently_observed",
        }
    )
    if sum(map(len, artifacts.values())) > MAX_TOTAL_BYTES or any(
        len(d) > MAX_ANSWERS_BYTES for d in artifacts.values()
    ):
        raise ValueError("adapter publication budget exceeded")
    return artifacts


def publish(snapshots: dict[str, bytes], output_dir: Path) -> dict[str, object]:
    artifacts = build_artifacts(snapshots)
    out = EvidenceStore(output_dir)
    out.prepare(new=True)
    with pinned_directory(out.root):
        with pinned_directory(out.root / "source", create=True):
            pass
        for name, data in artifacts.items():
            out.atomic_bytes(name, data)
        out.atomic_json(
            "manifest.json",
            AdapterManifest(
                source_fingerprint=digest(encoded({k: digest(v) for k, v in snapshots.items()})),
                files={
                    k: PublicationFile(sha256=digest(v), size_bytes=len(v))
                    for k, v in artifacts.items()
                },
            ).model_dump(mode="json"),
        )
    result = unique_json(artifacts["summary.json"])
    if not isinstance(result, dict):
        raise ValueError("invalid adapter summary")
    return result


def verify_publication(directory: Path) -> dict[str, object]:
    source = EvidenceStore(directory)
    manifest = AdapterManifest.model_validate(
        unique_json(source.read_bytes("manifest.json", limit=MAX_METADATA_BYTES))
    )
    snapshots = {
        name: source.read_bytes(
            f"source/{name}",
            limit=MAX_ANSWERS_BYTES if name == "answers.jsonl" else MAX_METADATA_BYTES,
        )
        for name in INPUT_FILES
    }
    artifacts = build_artifacts(snapshots)
    if set(artifacts) != set(manifest.files) or manifest.source_fingerprint != digest(
        encoded({k: digest(v) for k, v in snapshots.items()})
    ):
        raise ValueError("adapter manifest mismatch")
    for name, data in artifacts.items():
        ref = manifest.files[name]
        if (
            ref.sha256 != digest(data)
            or ref.size_bytes != len(data)
            or source.read_bytes(name, limit=len(data)) != data
        ):
            raise ValueError("adapter publication does not match replay")
    result = unique_json(artifacts["summary.json"])
    if not isinstance(result, dict):
        raise ValueError("invalid adapter summary")
    return result
