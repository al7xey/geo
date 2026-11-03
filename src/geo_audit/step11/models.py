"""Facts, estimates and suggested actions stay separate from causal hypotheses."""

from typing import Literal

from pydantic import Field

from ..contracts import Contract, Identifier
from ..step10.models import GapSuggestion


class Basis(Contract):
    source: Literal["aggregate", "question", "check", "gap", "source", "context"]
    key: str = Field(min_length=1, max_length=4096)


class Finding(Contract):
    finding_id: Identifier
    category: Literal["observed_fact", "calculated_estimate"]
    title: str
    description: str
    basis: list[Basis] = Field(min_length=1, max_length=20)
    number_ids: list[Identifier] = Field(default_factory=list)


class Action(Contract):
    action_id: Identifier
    period: Literal["week1", "weeks2_4", "later"]
    title: str
    instruction: str
    basis: list[Basis] = Field(min_length=1, max_length=20)
    question_ids: list[Identifier]
    material: GapSuggestion | None = None
    verify: str
    confidence: Literal["observed", "needs_review"]
    limitation: str


class Findings(Contract):
    schema_version: Literal["step11-findings-1.0"] = "step11-findings-1.0"
    items: list[Finding] = Field(max_length=4)


class Plan(Contract):
    schema_version: Literal["step11-plan-1.0"] = "step11-plan-1.0"
    synthetic: bool
    actions: list[Action] = Field(max_length=40)
    limitations: list[str]
    network_requests: Literal[0] = 0
    model_calls: Literal[0] = 0


SCHEMA_MODELS = (Basis, Finding, Action, Findings, Plan)
