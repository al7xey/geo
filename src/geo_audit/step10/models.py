"""Gap drafting is a suggestion, never proof that a page is missing."""

from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from ..contracts import SHA256, Contract, Identifier


class OutlineItem(Contract):
    text: str = Field(min_length=1, max_length=500)
    fact_ids: list[Identifier] = Field(min_length=1, max_length=10)


class GapSuggestion(Contract):
    topic_id: Identifier
    kind: Literal["existing_page", "new_page"]
    page_id: Identifier | None = None
    quote: str | None = Field(default=None, max_length=2000)
    title: str = Field(min_length=1, max_length=200)
    outline: list[OutlineItem] = Field(default_factory=list, max_length=5)


class GapResponse(Contract):
    schema_version: Literal["gap-response-1.0"] = "gap-response-1.0"
    job_id: Identifier
    context_sha256: SHA256
    reviewer_kind: Literal["manual", "model", "synthetic"]
    reviewer_model: str | None = Field(default=None, max_length=200)
    suggestions: list[GapSuggestion] = Field(max_length=6)

    @model_validator(mode="after")
    def provenance(self) -> Self:
        if self.reviewer_kind == "model" and not (self.reviewer_model or "").strip():
            raise ValueError("model reviewer metadata is required")
        return self


class GapTopic(Contract):
    topic_id: Identifier
    title: str
    question_ids: list[Identifier] = Field(min_length=1)
    candidate_page_ids: list[Identifier]
    grouping_method: Literal["bounded_lexical_candidates"] = "bounded_lexical_candidates"
    status: Literal["pending_review", "draft_material"]
    material: GapSuggestion | None = None
    material_provenance: Literal["manual", "model", "synthetic"] | None = None
    limitation: str


class GapJob(Contract):
    job_id: Identifier
    context_sha256: SHA256
    instruction_version: Literal["gap-draft-1"] = "gap-draft-1"
    status: Literal["pending_api_key", "needs_site_context"]
    executed: Literal[False] = False
    payload: dict[str, JsonValue]
    payload_chars: int = Field(ge=0, le=24000)
    max_output_tokens: Literal[1600] = 1600
    retry_limit: Literal[1] = 1
    synthetic: bool

    @model_validator(mode="after")
    def payload_binding(self) -> Self:
        from ..step5.pipeline import digest, encoded

        data = encoded(self.payload)
        sha = digest(data)
        if (
            self.context_sha256 != sha
            or self.job_id != "gap-job-" + sha[:24]
            or self.payload_chars != len(data.decode())
        ):
            raise ValueError("gap payload identity or size mismatch")
        return self


class Gaps(Contract):
    schema_version: Literal["step10-1.0"] = "step10-1.0"
    status: Literal["not_needed", "pending_review", "draft_material"]
    synthetic: bool
    candidate_question_ids: list[Identifier]
    topics: list[GapTopic] = Field(max_length=6)
    job_id: Identifier | None
    warnings: list[str]
    network_requests: Literal[0] = 0
    model_calls: Literal[0] = 0


SCHEMA_MODELS = (OutlineItem, GapSuggestion, GapResponse, GapTopic, GapJob, Gaps)
