"""Versioned step 5 contracts, independent of the upstream collector's runtime."""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from ..contracts import SHA256, Contract, Identifier
from ..urls import normalize_host

Group = Literal["A", "B", "C", "D"]
Delivery = Literal["ok", "empty", "error", "truncated"]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]


class Prompt(Contract):
    prompt_id: Identifier
    group: Group
    text: str = Field(min_length=1, max_length=10000)
    active: bool = True
    visibility_eligible: bool  # Unknown eligibility must not silently become True.

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank prompt")
        return value


class PromptSet(Contract):
    schema_version: Literal["1.0"]
    prompt_set_id: Identifier
    country: ShortText
    language: ShortText
    synthetic: bool = False
    prompts: list[Prompt] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        if len({p.prompt_id for p in self.prompts}) != len(self.prompts):
            raise ValueError("duplicate prompt ID")
        return self


class Target(Contract):
    target_id: Identifier
    name: ShortText
    aliases: list[ShortText] = Field(min_length=1, max_length=100)
    ambiguous_aliases: list[ShortText] = Field(default_factory=list, max_length=100)
    domains: list[ShortText] = Field(min_length=1, max_length=100)
    synthetic: bool = False
    note: str | None = Field(default=None, max_length=10000)

    @field_validator("name", "aliases", "ambiguous_aliases", "domains")
    @classmethod
    def not_blank(cls, value: str | list[str]) -> str | list[str]:
        items = [value] if isinstance(value, str) else value
        if any(not item.strip() for item in items):
            raise ValueError("blank target field")
        return value

    @field_validator("domains")
    @classmethod
    def normalized_domains(cls, domains: list[str]) -> list[str]:
        return list(dict.fromkeys(normalize_host(domain) for domain in domains))

    @model_validator(mode="after")
    def known_ambiguities(self) -> Self:
        if not set(self.ambiguous_aliases) <= set(self.aliases):
            raise ValueError("ambiguous aliases must be explicitly listed aliases")
        return self


class Citation(Contract):
    url: str = Field(min_length=1, max_length=4096)
    title: str | None = Field(default=None, max_length=10000)
    origin: ShortText


class Answer(Contract):
    answer_id: Identifier
    prompt_id: Identifier
    prompt_set_id: Identifier
    group: Group | None = None
    provider: ShortText
    model: ShortText
    origin: Literal["synthetic", "manual_chat", "api"]
    synthetic: bool = False
    repeat_index: int = Field(ge=1, le=10000)
    collection_eligible: bool = True
    collected_at: str | None = Field(default=None, max_length=100)
    search_mode: ShortText
    country: ShortText | None = None
    language: ShortText | None = None
    settings: dict[str, str | int | bool | None] = Field(default_factory=dict, max_length=100)
    status: Delivery
    text: str = Field(max_length=500000)
    sources: list[Citation] = Field(default_factory=list, max_length=200)
    raw_ref: str | None = Field(default=None, max_length=1000)
    text_sha256: SHA256 | None = None
    error: dict[str, str] | None = None


class MentionCandidate(Contract):
    alias: str
    quote: str
    start: int
    end: int
    ambiguous: bool
    location: Literal["body", "text_url"]


class SourceObservation(Contract):
    url: str
    host: str
    registrable_domain: str | None
    psl_known: bool
    target_host_match: bool
    origins: list[str]
    text_spans: list[list[int]]
    citation_indices: list[int] = Field(default_factory=list)
    resolution_status: Literal["not_attempted"] = "not_attempted"


class MechanicalAnswer(Contract):
    answer_id: Identifier
    prompt_id: Identifier
    group: Group
    provider: str
    model: str
    origin: str
    repeat_index: int
    status: Delivery
    source_line: int
    text_sha256: SHA256
    text_chars: int
    visibility_eligible: bool
    candidates: list[MentionCandidate]
    sources: list[SourceObservation]
    invalid_source_count: int
    target_name_candidate: bool
    target_domain_in_body: bool
    target_domain_in_citations: bool
    annotation_status: Literal["unreviewed"] = "unreviewed"


class IntakeRecord(Contract):
    line: int
    line_sha256: SHA256
    answer_id: str | None
    outcome: Literal["accepted", "duplicate", "quarantined"]
    reason: str | None = None


class ObservedFact(Contract):
    """A fact ABOUT a collected answer, never a verified claim about the business."""

    fact_id: Identifier
    category: Literal["observed_fact"] = "observed_fact"
    kind: Literal["answer_record", "name_match"]
    answer_id: Identifier
    source_line: int
    text_sha256: SHA256
    quote: str | None = None
    start: int | None = None
    end: int | None = None
    ambiguous_identity: bool | None = None
    location: Literal["body", "text_url"] | None = None
    delivery_status: Delivery
    scope: Literal["collected_answer_only"] = "collected_answer_only"


class Step5Summary(Contract):
    schema_version: Literal["step5-1.0"] = "step5-1.0"
    algorithm_version: Literal["step5-rules-2"] = "step5-rules-2"
    status: Literal["complete", "partial"]
    input_lines: int
    accepted: int
    duplicates: int
    quarantined: int
    prompt_groups: dict[str, int]
    evidence_categories: dict[str, int]
    delivery_statuses: dict[str, int]
    target_name_candidates: int
    target_citation_host_matches: int
    ambiguous_name_candidates: int = 0
    invalid_sources: int = 0
    warnings: list[str] = Field(default_factory=list)
    network_requests: Literal[0] = 0
    model_calls: Literal[0] = 0


class PublicationFile(Contract):
    sha256: SHA256
    size_bytes: int = Field(ge=0, le=64 * 1024 * 1024)


class PublicationManifest(Contract):
    schema_version: Literal["step5-publication-1.1"] = "step5-publication-1.1"
    committed: Literal[True] = True
    algorithm_version: Literal["step5-rules-2"] = "step5-rules-2"
    psl_sha256: SHA256
    input_fingerprint: SHA256
    files: dict[str, PublicationFile] = Field(min_length=1, max_length=32)


SCHEMA_MODELS: tuple[type[Contract], ...] = (
    PromptSet,
    Target,
    Answer,
    MechanicalAnswer,
    IntakeRecord,
    ObservedFact,
    Step5Summary,
    PublicationManifest,
)
