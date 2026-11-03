"""Step 6 decisions distinguish uncertainty, delivery and rule provenance."""

import hashlib
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ..contracts import SHA256, Contract, Identifier
from ..step5.models import Delivery, Group, PublicationFile, Target

Decision = Literal["yes", "no", "uncertain"]
Count = Annotated[int, Field(ge=0)]
RequestedField = Literal["target_presence", "target_recommended", "target_discouraged", "brands"]


class Evidence(Contract):
    quote: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    rule: str = Field(min_length=1)

    @model_validator(mode="after")
    def span_length(self) -> Self:
        if self.end - self.start != len(self.quote):
            raise ValueError("evidence length mismatch")
        return self


class RoleClaim(Contract):
    role: Literal["recommended", "discouraged", "mentioned"]
    evidence: Evidence
    context_evidence: Evidence | None = None


class BrandAnnotation(Contract):
    name: str = Field(min_length=1, max_length=200)
    is_target: Decision
    presence_evidence: list[Evidence] = Field(min_length=1)
    roles: list[RoleClaim] = Field(min_length=1)
    recommended: Decision
    discouraged: Decision
    rank: int | None = Field(default=None, ge=1)
    rank_evidence: Evidence | None = None
    rank_context_evidence: Evidence | None = None
    identity_basis: Literal["target_dictionary", "structured_name_candidate"]
    reason_codes: list[str]
    method: Literal["rules"] = "rules"

    @model_validator(mode="after")
    def justified(self) -> Self:
        for field, role in ((self.recommended, "recommended"), (self.discouraged, "discouraged")):
            if (field == "yes") != any(claim.role == role for claim in self.roles):
                raise ValueError("role decision disagrees with evidence")
        if (self.rank is not None) != (self.rank_evidence is not None):
            raise ValueError("rank requires evidence")
        if (self.rank is not None) != (self.rank_context_evidence is not None):
            raise ValueError("rank requires explicit ranking context")
        return self


class Annotation(Contract):
    schema_version: Literal["step6-1.1"] = "step6-1.1"
    answer_id: Identifier
    prompt_id: Identifier
    group: Group
    provider: str
    model: str
    origin: str
    source_line: int = Field(ge=1)
    text_sha256: SHA256
    delivery_status: Delivery
    measurement_eligible: bool
    answer_kind: Literal["list", "prose", "refusal", "mixed", "unknown"] | None
    relevance: Literal["unassessed"] = "unassessed"
    target_presence: Decision | None
    target_recommended: Decision | None
    target_discouraged: Decision | None
    target_cited: Decision | None
    brands: list[BrandAnnotation]
    reason_codes: list[str]
    needs_model: bool
    status: Literal["rules_complete", "pending_model", "not_eligible"]
    method: Literal["rules"] = "rules"
    independent_review: Literal["not_performed"] = "not_performed"
    competitor_extraction: Literal["structured_candidates_only"] = "structured_candidates_only"

    @model_validator(mode="after")
    def consistent(self) -> Self:
        values = (
            self.target_presence,
            self.target_recommended,
            self.target_discouraged,
            self.target_cited,
        )
        if self.delivery_status != "ok":
            if (
                any(v is not None for v in values)
                or self.brands
                or self.needs_model
                or self.measurement_eligible
                or self.status != "not_eligible"
                or self.answer_kind is not None
            ):
                raise ValueError("failed delivery must not yield semantic decisions")
        elif any(v is None for v in values):
            raise ValueError("delivered answer needs decisions, possibly uncertain")
        elif self.status == "not_eligible":
            raise ValueError("delivered answer cannot have a delivery-failure status")
        if self.target_recommended == "yes" and not any(
            b.is_target == "yes" and b.recommended == "yes" for b in self.brands
        ):
            raise ValueError("positive target recommendation lacks a brand claim")
        if self.target_discouraged == "yes" and not any(
            b.is_target == "yes" and b.discouraged == "yes" for b in self.brands
        ):
            raise ValueError("negative target recommendation lacks a brand claim")
        if self.needs_model != (self.status == "pending_model"):
            raise ValueError("pending model status mismatch")
        unresolved = any(v == "uncertain" for v in values) or any(
            b.recommended == "uncertain"
            or b.discouraged == "uncertain"
            or "conflicting_ranks" in b.reason_codes
            for b in self.brands
        )
        if self.needs_model != unresolved:
            raise ValueError("uncertainty must remain pending")
        if self.target_presence == "yes" and not any(b.is_target == "yes" for b in self.brands):
            raise ValueError("target identity lacks a brand observation")
        if self.target_presence != "yes" and any(b.is_target == "yes" for b in self.brands):
            raise ValueError("target presence disagrees with brand identity")
        if self.target_presence != "yes" and (
            self.target_recommended == "yes" or self.target_discouraged == "yes"
        ):
            raise ValueError("recommendation needs a resolved target identity")
        return self


class SemanticJob(Contract):
    schema_version: Literal["step6-job-1.1"] = "step6-job-1.1"
    job_id: Identifier
    answer_id: Identifier
    task_type: Literal["semantic_annotation"] = "semantic_annotation"
    prompt_version: Literal["semantic-2"] = "semantic-2"
    status: Literal["pending_api_key"] = "pending_api_key"
    executed: Literal[False] = False
    text_sha256: SHA256
    text: str
    prompt_text: str
    target: Target
    requested_fields: list[RequestedField] = Field(min_length=1, max_length=4)
    synthetic: bool
    instruction: str

    @model_validator(mode="after")
    def valid_job(self) -> Self:
        if hashlib.sha256(self.text.encode()).hexdigest() != self.text_sha256:
            raise ValueError("semantic job text hash mismatch")
        if len(set(self.requested_fields)) != len(self.requested_fields):
            raise ValueError("duplicate semantic fields")
        if self.target.note is not None:
            raise ValueError("semantic job must omit profile authoring notes")
        return self


class Step6Summary(Contract):
    schema_version: Literal["step6-1.1"] = "step6-1.1"
    algorithm_version: Literal["step6-rules-2"] = "step6-rules-2"
    status: Literal["complete", "partial"]
    input_answers: Count
    annotated: Count
    not_eligible: Count
    rules_complete: Count
    pending_model: Count
    target_presence: dict[str, Count]
    target_recommended: dict[str, Count]
    target_discouraged: dict[str, Count]
    prompt_groups: dict[str, Count]
    pending_reasons: dict[str, Count] = Field(default_factory=dict)
    independent_review: Literal["not_performed"] = "not_performed"
    network_requests: Literal[0] = 0
    model_calls: Literal[0] = 0

    @model_validator(mode="after")
    def accounting(self) -> Self:
        if self.annotated != self.input_answers or (
            self.rules_complete + self.pending_model + self.not_eligible != self.annotated
        ):
            raise ValueError("annotation accounting mismatch")
        for counts in (self.target_presence, self.target_recommended, self.target_discouraged):
            if (
                set(counts) != {"yes", "no", "uncertain"}
                or sum(counts.values()) != self.annotated - self.not_eligible
            ):
                raise ValueError("decision accounting mismatch")
        if (
            set(self.prompt_groups) != set("ABCD")
            or sum(self.prompt_groups.values()) != self.annotated
        ):
            raise ValueError("prompt group accounting mismatch")
        return self


class PublicationManifest(Contract):
    schema_version: Literal["step6-publication-1.1"] = "step6-publication-1.1"
    committed: Literal[True] = True
    algorithm_version: Literal["step6-rules-2"] = "step6-rules-2"
    source_fingerprint: SHA256
    files: dict[str, PublicationFile] = Field(min_length=1, max_length=24)


SCHEMA_MODELS: tuple[type[Contract], ...] = (
    Annotation,
    SemanticJob,
    Step6Summary,
    PublicationManifest,
)
