"""Blind review contracts. No schema contains API credentials or live execution."""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from ..contracts import SHA256, Contract, Identifier
from ..step5.models import PublicationFile
from ..step6.models import Decision

Count = Annotated[int, Field(ge=0)]


class ReviewPolicy(Contract):
    schema_version: Literal["step7-policy-1.0"] = "step7-policy-1.0"
    seed: int = Field(default=20261103, ge=0, le=2**32 - 1)
    sample_percent: Literal[5] = 5
    minimum_comparable: int = Field(default=20, ge=1, le=10000)
    minimum_distinct_answers: int | None = Field(default=None, ge=1, le=10000)
    minimum_comparison_coverage: float = Field(default=0.8, ge=0, le=1)
    agreement_threshold_percent: Literal[90] = 90


class TargetContext(Contract):
    name: str
    aliases: list[str]
    ambiguous_aliases: list[str]
    domains: list[str]


class BlindJob(Contract):
    schema_version: Literal["step7-blind-job-1.0"] = "step7-blind-job-1.0"
    job_id: Identifier
    text_sha256: SHA256
    text: str
    prompt_text: str
    target: TargetContext
    instruction: str
    response_schema: Literal["step7-review-1.0"] = "step7-review-1.0"


class SelectedAnswer(Contract):
    answer_id: Identifier
    job_id: Identifier
    random_sample: bool
    uncertain_case: bool


class Quote(Contract):
    quote: str = Field(min_length=1, max_length=500000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def length(self) -> Self:
        if self.end - self.start != len(self.quote):
            raise ValueError("review quote length mismatch")
        return self


class Reviewer(Contract):
    kind: Literal["model", "manual", "synthetic"]
    provider: str = Field(min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    family: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def declared_model(self) -> Self:
        if not self.provider.strip():
            raise ValueError("review provider must not be blank")
        if self.kind == "model" and (
            not self.model or not self.model.strip() or not self.family or not self.family.strip()
        ):
            raise ValueError("model review requires declared model and family")
        return self


class ReviewedBrand(Contract):
    name: str = Field(min_length=1, max_length=200)
    is_target: Decision
    recommended: Decision
    discouraged: Decision
    identity_evidence: list[Quote] = Field(min_length=1, max_length=2000)
    recommendation_evidence: list[Quote] = Field(default_factory=list, max_length=2000)
    discouragement_evidence: list[Quote] = Field(default_factory=list, max_length=2000)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reviewed brand name must not be blank")
        return value

    @model_validator(mode="after")
    def evidence_for_positive(self) -> Self:
        if self.recommended == "yes" and not self.recommendation_evidence:
            raise ValueError("recommendation evidence missing")
        if self.discouraged == "yes" and not self.discouragement_evidence:
            raise ValueError("discouragement evidence missing")
        return self


class ReviewResult(Contract):
    schema_version: Literal["step7-review-1.0"] = "step7-review-1.0"
    job_id: Identifier
    text_sha256: SHA256
    reviewer: Reviewer
    status: Literal["ok", "error"]
    target_presence: Decision | None
    target_recommended: Decision | None
    target_discouraged: Decision | None
    brands: list[ReviewedBrand] = Field(default_factory=list, max_length=1000)
    error_code: Literal["network", "rate_limit", "refusal", "invalid_response"] | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        values = (self.target_presence, self.target_recommended, self.target_discouraged)
        if self.status == "error":
            if any(v is not None for v in values) or self.brands or not self.error_code:
                raise ValueError("error result must not carry semantic decisions")
            return self
        if any(v is None for v in values) or self.error_code:
            raise ValueError("successful review requires decisions")
        target = [b for b in self.brands if b.is_target == "yes"]
        if len(target) > 1 or (self.target_presence == "yes") != bool(target):
            raise ValueError("target identity conflicts with brand evidence")
        for value, field in (
            (self.target_recommended, "recommended"),
            (self.target_discouraged, "discouraged"),
        ):
            positive = any(getattr(b, field) == "yes" for b in target)
            if (value == "yes") != positive:
                raise ValueError("target role conflicts with brand review")
        if self.target_presence != "yes" and any(v == "yes" for v in values[1:]):
            raise ValueError("positive role requires resolved identity")
        if target and (
            self.target_recommended != target[0].recommended
            or self.target_discouraged != target[0].discouraged
        ):
            raise ValueError("target role summary must match the target brand")
        if self.target_presence == "no" and (
            any(b.is_target != "no" for b in self.brands)
            or self.target_recommended != "no"
            or self.target_discouraged != "no"
        ):
            raise ValueError("confident absence requires consistent absent target roles")
        return self


class AgreementMetric(Contract):
    assigned_comparisons: Count
    comparable: Count
    comparable_answers: Count
    matches: Count
    disagreements: Count
    comparison_coverage: float | None
    agreement_percent: float | None
    reliability: Literal["not_assessed", "insufficient_sample", "medium", "low"]
    basis: Literal["declared_model_imports_only"] = "declared_model_imports_only"

    @model_validator(mode="after")
    def arithmetic(self) -> Self:
        if (
            self.matches + self.disagreements != self.comparable
            or self.comparable > self.assigned_comparisons
            or self.comparable_answers > self.comparable
            or bool(self.comparable_answers) != bool(self.comparable)
        ):
            raise ValueError("agreement comparison accounting mismatch")
        percent = 100 * self.matches / self.comparable if self.comparable else None
        coverage = (
            self.comparable / self.assigned_comparisons if self.assigned_comparisons else None
        )
        if self.agreement_percent != percent or self.comparison_coverage != coverage:
            raise ValueError("agreement rate does not match counts")
        if self.comparable == 0 and self.reliability != "not_assessed":
            raise ValueError("no comparisons cannot imply reliability")
        return self


class Step7Summary(Contract):
    schema_version: Literal["step7-1.1"] = "step7-1.1"
    algorithm_version: Literal["step7-rules-2"] = "step7-rules-2"
    status: Literal["pending_api_key", "partial", "complete"]
    eligible_answers: Count
    synthetic_input_answers: Count = 0
    random_sample_count: Count
    uncertain_count: Count
    overlap_count: Count
    selected_count: Count
    accepted_results: Count
    quarantined_rows: Count
    duplicate_rows: Count
    model_reviews: Count
    manual_reviews: Count
    synthetic_reviews: Count
    error_results: Count
    pending_jobs: Count
    resolved_fields: Count
    unresolved_answers: Count = 0
    conflicting_answers: Count = 0
    rank_outside_scope_answers: Count = 0
    model_reviewer: Reviewer | None = None
    independent_review: Literal["not_performed", "imported_declarations_only"]
    metrics: dict[str, AgreementMetric]
    network_requests: Literal[0] = 0
    model_calls: Literal[0] = 0

    @model_validator(mode="after")
    def accounting(self) -> Self:
        if (
            self.random_sample_count + self.uncertain_count - self.overlap_count
            != self.selected_count
            or self.selected_count > self.eligible_answers
            or self.overlap_count > min(self.random_sample_count, self.uncertain_count)
        ):
            raise ValueError("review selection accounting mismatch")
        if self.random_sample_count != (self.eligible_answers * 5 + 99) // 100:
            raise ValueError("random sample must cover ceil(5 percent)")
        if (
            self.accepted_results
            != self.model_reviews
            + self.manual_reviews
            + self.synthetic_reviews
            + self.error_results
            or self.pending_jobs != self.selected_count - self.model_reviews - self.manual_reviews
        ):
            raise ValueError("review result accounting mismatch")
        if self.model_reviews == 0 and any(m.comparable for m in self.metrics.values()):
            raise ValueError("model agreement requires model imports")
        if self.model_reviews and (
            self.model_reviewer is None or self.model_reviewer.kind != "model"
        ):
            raise ValueError("model agreement requires a single declared reviewer")
        if not self.model_reviews and self.model_reviewer is not None:
            raise ValueError("reviewer without model reviews")
        if set(self.metrics) != {"identity", "target_roles", "competitor_roles"}:
            raise ValueError("incomplete agreement metrics")
        if self.status == "complete" and (
            self.pending_jobs or self.quarantined_rows or self.error_results
        ):
            raise ValueError("incomplete or quarantined import cannot be complete")
        return self


class BrandOverride(Contract):
    primary_brand_index: Count
    reviewed_name: str = Field(min_length=1, max_length=200)
    changes: dict[Literal["is_target", "recommended", "discouraged"], Literal["yes", "no"]] = Field(
        min_length=1, max_length=3
    )
    review_job_id: Identifier


class BrandDisagreement(Contract):
    primary_brand_index: Count
    reviewed_name: str = Field(min_length=1, max_length=200)
    fields: list[Literal["is_target", "recommended", "discouraged"]] = Field(
        min_length=1, max_length=3
    )
    review_job_id: Identifier


class ResolvedAnnotation(Contract):
    schema_version: Literal["step7-effective-1.1"] = "step7-effective-1.1"
    answer_id: Identifier
    primary_text_sha256: SHA256
    primary_annotation_sha256: SHA256
    target_presence: Decision | None
    target_recommended: Decision | None
    target_discouraged: Decision | None
    target_cited: Decision | None
    measurement_eligible: bool
    field_sources: dict[str, Literal["primary_rules", "imported_model", "imported_manual"]]
    brand_overrides: list[BrandOverride]
    brand_disagreements: list[BrandDisagreement] = Field(default_factory=list)
    outstanding_fields: list[str]
    confident_disagreements: list[
        Literal["target_presence", "target_recommended", "target_discouraged"]
    ]
    review_job_id: Identifier | None
    review_state: Literal["pending", "not_selected", "imported_model", "imported_manual"]

    @model_validator(mode="after")
    def provenance(self) -> Self:
        if set(self.field_sources) != {
            "target_presence",
            "target_recommended",
            "target_discouraged",
        }:
            raise ValueError("effective field provenance is incomplete")
        changed = (
            any(value != "primary_rules" for value in self.field_sources.values())
            or self.brand_overrides
        )
        if changed and (
            not self.review_job_id or self.review_state not in {"imported_model", "imported_manual"}
        ):
            raise ValueError("effective override lacks an imported review")
        if (self.review_state == "not_selected") != (self.review_job_id is None):
            raise ValueError("review state disagrees with job provenance")
        for source in self.field_sources.values():
            if source != "primary_rules" and source != self.review_state:
                raise ValueError("mixed field reviewer provenance")
        if len({b.primary_brand_index for b in self.brand_overrides}) != len(self.brand_overrides):
            raise ValueError("duplicate brand override")
        if any(b.review_job_id != self.review_job_id for b in self.brand_overrides):
            raise ValueError("brand override belongs to another review")
        if any(b.review_job_id != self.review_job_id for b in self.brand_disagreements):
            raise ValueError("brand disagreement belongs to another review")
        if (self.confident_disagreements or self.brand_disagreements) and self.review_state not in {
            "imported_model",
            "imported_manual",
        }:
            raise ValueError("disagreement requires an imported reviewer")
        for field in ("target_presence", "target_recommended", "target_discouraged"):
            if (getattr(self, field) == "uncertain") != (field in self.outstanding_fields):
                raise ValueError("effective uncertainty is not accounted")
        if self.target_presence != "yes" and (
            self.target_recommended == "yes" or self.target_discouraged == "yes"
        ):
            raise ValueError("effective positive role lacks resolved target identity")
        return self


class PublicationManifest(Contract):
    schema_version: Literal["step7-publication-1.1"] = "step7-publication-1.1"
    committed: Literal[True] = True
    algorithm_version: Literal["step7-rules-2"] = "step7-rules-2"
    source_fingerprint: SHA256
    files: dict[str, PublicationFile] = Field(min_length=1, max_length=32)


SCHEMA_MODELS: tuple[type[Contract], ...] = (
    ReviewPolicy,
    BlindJob,
    ReviewResult,
    Step7Summary,
    ResolvedAnnotation,
    PublicationManifest,
)

SCHEMA_VERSIONS = {"Step7Summary": "1.1", "ResolvedAnnotation": "1.1", "PublicationManifest": "1.1"}
