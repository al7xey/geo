"""Separate schema namespace; the step 0 registry and URNs remain unchanged."""

from ..contracts import Contract
from .config import Step1Config
from .models import (
    Candidate,
    ChecksResult,
    Coverage,
    CrawlResult,
    DiagnosticResult,
    Material,
    Page,
    QuoteRef,
    RobotsDecision,
    RobotsObservation,
    RunResult,
    SitemapObservation,
    Step1Evidence,
    Step1Result,
    Step2Input,
)

MODELS: dict[str, type[Contract]] = {
    model.__name__: model
    for model in (
        Step1Config,
        Candidate,
        ChecksResult,
        Coverage,
        CrawlResult,
        DiagnosticResult,
        Material,
        Page,
        QuoteRef,
        RobotsDecision,
        RobotsObservation,
        RunResult,
        SitemapObservation,
        Step1Evidence,
        Step1Result,
        Step2Input,
    )
}


def schema_for(name: str) -> dict[str, object]:
    schema = MODELS[name].model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:geo-audit:{name}:1.0.0"
    return schema
