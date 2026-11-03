"""Versioned measurement contracts. A percentage is never a model probability."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ..contracts import SHA256, Contract, Identifier
from ..step1.models import TechnicalCheck
from ..step5.models import Group, Target
from ..step6.models import Decision
from ..step7.models import AgreementMetric

Count = Annotated[int, Field(ge=0)]
Percent = Annotated[float, Field(ge=0, le=100)]
MetricName = Literal["name_match", "presence", "recommended", "discouraged", "cited"]


class MetricPolicy(Contract):
    schema_version: Literal["step9-policy-1.0"] = "step9-policy-1.0"
    bootstrap_replicates: int = Field(default=400, ge=100, le=2000)
    seed: int = Field(default=20261103, ge=0, le=2**32 - 1)
    max_cohorts: int = Field(default=100, ge=1, le=100)
    max_source_pages: Literal[5] = 5


class PageContext(Contract):
    page_id: Identifier
    url: str = Field(max_length=4096)
    title: str = Field(max_length=1000)
    text: str = Field(max_length=20000)
    source_text_sha256: SHA256
    truncated: bool


class CompanyFact(Contract):
    fact_id: Identifier
    text: str = Field(min_length=1, max_length=1000)
    page_id: Identifier
    quote: str = Field(min_length=1, max_length=2000)


class SiteContext(Contract):
    schema_version: Literal["site-context-1.0"] = "site-context-1.0"
    status: Literal["not_supplied", "verified_step1"] = "not_supplied"
    source_commit_sha256: SHA256 | None = None
    pages: list[PageContext] = Field(default_factory=list, max_length=30)
    checks: list[TechnicalCheck] = Field(default_factory=list, max_length=5000)
    facts: list[CompanyFact] = Field(default_factory=list, max_length=100)
    limitations: list[str] = Field(default_factory=lambda: ["Технический обход сайта не передан."])

    @model_validator(mode="after")
    def provenance(self) -> Self:
        if self.status == "not_supplied" and (
            self.pages or self.checks or self.facts or self.source_commit_sha256
        ):
            raise ValueError("unverified site data")
        if self.status == "verified_step1" and not self.source_commit_sha256:
            raise ValueError("missing site commit")
        pages = {page.page_id: page for page in self.pages}
        if len(pages) != len(self.pages) or len({f.fact_id for f in self.facts}) != len(self.facts):
            raise ValueError("duplicate site context identity")
        for fact in self.facts:
            if fact.page_id not in pages or fact.quote not in pages[fact.page_id].text:
                raise ValueError("company fact lacks a literal page quote")
        return self


class AnswerMeasure(Contract):
    answer_id: Identifier
    prompt_id: Identifier
    group: Group
    cohort_id: Identifier
    provider: str
    model: str
    synthetic: bool
    status: str
    eligible: bool
    excluded_reasons: list[str]
    name_match: Decision | None
    presence: Decision | None
    recommended: Decision | None
    discouraged: Decision | None
    cited: Decision | None
    other_recommended: bool


class QuestionMetric(Contract):
    metric: MetricName
    successes: Count
    denominator: Count
    unknown: Count
    percent: Percent | None

    @model_validator(mode="after")
    def arithmetic(self) -> Self:
        if self.successes > self.denominator:
            raise ValueError("invalid metric numerator")
        expected = 100.0 * self.successes / self.denominator if self.denominator else None
        if self.percent != expected:
            raise ValueError("question percentage cannot be reproduced")
        return self


class QuestionResult(Contract):
    prompt_id: Identifier
    text: str = Field(max_length=10000)
    group: Group
    organic: bool
    valid_answers: Count
    target_confirmed: Count
    target_uncertain: Count
    name_matches: Count
    other_recommended_answers: Count
    other_recommended_percent: Percent | None
    metrics: list[QuestionMetric]
    stability: Literal["always", "sometimes", "never", "uncertain", "no_data"]

    @model_validator(mode="after")
    def arithmetic(self) -> Self:
        if (
            self.target_confirmed + self.target_uncertain > self.valid_answers
            or self.name_matches > self.valid_answers
            or self.other_recommended_answers > self.valid_answers
        ):
            raise ValueError("question counts exceed eligible answers")
        expected = (
            100.0 * self.other_recommended_answers / self.valid_answers
            if self.valid_answers
            else None
        )
        if self.other_recommended_percent != expected:
            raise ValueError("other-candidate percentage differs from counts")
        if len(self.metrics) != 5 or len({m.metric for m in self.metrics}) != 5:
            raise ValueError("question metric set incomplete")
        return self


class Rate(Contract):
    metric_id: Identifier
    metric: MetricName
    scope: str
    cohort_id: Identifier | None = None
    group: Group | None = None
    percent: Percent | None
    interval_low: Percent | None
    interval_high: Percent | None
    questions: Count
    answered_questions: Count
    known_answers: Count
    unknown_answers: Count
    reliability: Literal["not_assessed", "insufficient_sample", "medium", "low"]
    method: Literal["equal_question_mean_cluster_bootstrap"] = (
        "equal_question_mean_cluster_bootstrap"
    )
    reliability_basis: Literal["overall_step7_sample_and_local_missingness"] = (
        "overall_step7_sample_and_local_missingness"
    )

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.answered_questions > self.questions or (self.percent is None) != (
            self.answered_questions == 0
        ):
            raise ValueError("invalid rate coverage")
        if (self.interval_low is None) != (self.interval_high is None):
            raise ValueError("incomplete bootstrap interval")
        if self.interval_low is not None and self.interval_high is not None:
            if self.interval_low > self.interval_high or self.answered_questions < 2:
                raise ValueError("invalid bootstrap interval")
        return self


class NumberCell(Contract):
    value: float | int
    unit: Literal["percent", "count"]
    basis: str


class Competitor(Contract):
    entity_id: Identifier
    name: str
    identity_scope: str
    answer_ids: list[Identifier]
    recommended_answer_ids: list[Identifier]
    share_percent: Percent | None
    candidate_only: bool


class SourceStat(Contract):
    host: str
    kind: str
    owner: str
    answer_ids: list[Identifier]
    urls: list[str]


class SourcePageTask(Contract):
    task_id: Identifier
    url: str
    answer_ids: list[Identifier]
    status: Literal["not_requested"] = "not_requested"
    purpose: Literal["literal_names_on_external_choice_page"] = (
        "literal_names_on_external_choice_page"
    )


class Cohort(Contract):
    provider: str
    model: str
    settings: dict[str, str | int | bool | None]


class LiteralName(Contract):
    name: str
    quote: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    ambiguous: bool


class SourceObservation(Contract):
    task_id: Identifier
    url: str
    status: Literal["observed", "not_observed"]
    publication_id: Identifier | None = None
    publication_sha256: SHA256 | None = None
    text: str = Field(default="", max_length=20000)
    text_sha256: SHA256
    matches: list[LiteralName] = Field(max_length=100)
    other_matches: list[LiteralName] = Field(default_factory=list, max_length=100)
    limitation: str


class SourceInspection(Contract):
    schema_version: Literal["source-inspection-1.0"] = "source-inspection-1.0"
    source_aggregates_sha256: SHA256
    selection_sha256: SHA256
    synthetic: bool
    observations: list[SourceObservation] = Field(max_length=5)
    network_attempts: int = Field(ge=0, le=30)
    attempt_count_complete: bool = True
    model_calls: Literal[0] = 0


class Aggregates(Contract):
    schema_version: Literal["step9-1.0"] = "step9-1.0"
    target: Target
    synthetic: bool
    policy: MetricPolicy
    total_answers: Count
    eligible_answers: Count
    excluded_answers: Count
    providers: dict[str, Count]
    cohorts: dict[str, Cohort]
    delivery_statuses: dict[str, Count]
    exclusion_reasons: dict[str, Count]
    questions: list[QuestionResult]
    rates: list[Rate]
    numbers: dict[str, NumberCell]
    competitors: list[Competitor]
    sources: list[SourceStat]
    source_page_tasks: list[SourcePageTask]
    review_metrics: dict[str, AgreementMetric]
    stability: dict[str, Count]
    technical_counts: dict[str, Count]
    source_inspection: SourceInspection | None = None
    warnings: list[str]
    network_requests: Literal[0] = 0
    model_calls: Literal[0] = 0

    @model_validator(mode="after")
    def accounting(self) -> Self:
        if (
            sum(self.providers.values()) != self.total_answers
            or sum(self.delivery_statuses.values()) != self.total_answers
            or self.eligible_answers + self.excluded_answers != self.total_answers
            or sum(q.valid_answers for q in self.questions) != self.eligible_answers
        ):
            raise ValueError("answer totals disagree")
        if len({q.prompt_id for q in self.questions}) != len(self.questions):
            raise ValueError("duplicate measured question")
        if len({r.metric_id for r in self.rates}) != len(self.rates):
            raise ValueError("duplicate rate identity")
        return self


SCHEMA_MODELS = (
    MetricPolicy,
    SiteContext,
    AnswerMeasure,
    QuestionMetric,
    QuestionResult,
    Rate,
    Competitor,
    SourceStat,
    SourcePageTask,
    SourceInspection,
    Aggregates,
)
