"Offline technical recommendations tied to verified evidence; no LLM/visibility claims."

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from .contracts import SHA256, Contract, Identifier, UTCDateTime
from .errors import problem
from .evidence import MAX_JSON_BYTES, EvidenceStore
from .handoff import load_site_result
from .models import SiteValidationResult
from .step1.models import Step1Result
from .step1.storage import _load_step1_result, digest


class Recommendation(Contract):
    recommendation_id: Identifier
    priority: Literal["high", "medium", "info"]
    category: Literal[
        "robots",
        "indexing",
        "https",
        "access",
        "snippets",
        "canonical",
        "material",
        "coverage",
        "javascript",
        "ua",
    ]
    title: str = Field(max_length=200)
    action: str = Field(max_length=1000)
    affected_checks: int = Field(ge=0)
    check_ids: tuple[Identifier, ...] = Field(max_length=128)
    evidence_ids: tuple[Identifier, ...] = Field(max_length=128)
    references_truncated: bool


class RecommendationReport(Contract):
    schema_version: Literal["recommendations-1.0.0"] = "recommendations-1.0.0"
    method_version: Literal["technical-geo-rules-1"] = "technical-geo-rules-1"
    scope: Literal["technical_only_no_ai_visibility_measurement"] = (
        "technical_only_no_ai_visibility_measurement"
    )
    source_step: Literal[0, 1]
    source_run_id: Identifier
    source_publication_id: Identifier
    source_commit_sha256: SHA256
    observed_at: UTCDateTime
    recommendations: tuple[Recommendation, ...] = Field(max_length=32)
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def identities(self) -> Self:
        ids = [r.recommendation_id for r in self.recommendations]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate recommendation identity")
        for item in self.recommendations:
            if len(set(item.check_ids)) != len(item.check_ids) or len(
                set(item.evidence_ids)
            ) != len(item.evidence_ids):
                raise ValueError("duplicate references")
            if item.affected_checks < len(item.check_ids):
                raise ValueError("reference count exceeds affected checks")
        return self


RULES = (
    (
        "robots_policy",
        "robots",
        "medium",
        "Review crawler access policy",
        (
            "Review the cited robots rules and delivery errors. Preserve "
            "intentional restrictions; do not bypass robots or authentication."
        ),
    ),
    (
        "indexing_directives",
        "indexing",
        "high",
        "Review indexing instructions",
        (
            "For pages intended for indexing, inspect the cited noindex/none "
            "directives and operator scope. Unknown observations require "
            "verification, not removal of restrictions."
        ),
    ),
    (
        "https_transport",
        "https",
        "high",
        "Verify HTTPS transport",
        (
            "Check certificate hostname, trust chain and server configuration. Keep"
            " TLS verification enabled. A timeout is not proof HTTPS is "
            "unavailable."
        ),
    ),
    (
        "observed_access",
        "access",
        "high",
        "Investigate observed HTTP access errors",
        (
            "Check the cited HTTP response and intended access policy. Resolve "
            "server errors or provide public material; do not evade CAPTCHA/login "
            "restrictions."
        ),
    ),
    (
        "snippet_directives",
        "snippets",
        "info",
        "Review snippet policy",
        (
            "Review nosnippet/data-nosnippet and supported operator controls. These"
            " are not a universal AI prohibition and may be intentional."
        ),
    ),
    (
        "canonical_signal",
        "canonical",
        "info",
        "Verify canonical declarations",
        (
            "Confirm publisher canonical targets and language variants are "
            "intentional. External canonical does not authorize crawling that host."
        ),
    ),
    (
        "javascript",
        "javascript",
        "medium",
        "Verify unavailable dynamic content",
        (
            "Inspect static-content limitations. Rendering requires a verified "
            "safe-egress adapter; do not bypass robots, CAPTCHA or login."
        ),
    ),
    (
        "ua_comparison",
        "ua",
        "info",
        "Review differences in observed UA responses",
        (
            "Compare status, final URL, MIME, challenge and content evidence. Our "
            "environment does not prove what real crawler infrastructure receives."
        ),
    ),
)


def _item(
    category: str,
    priority: str,
    title: str,
    action: str,
    checks: tuple[str, ...] = (),
    evidence: tuple[str, ...] = (),
) -> Recommendation:
    ids = tuple(sorted(set(checks)))
    refs = tuple(sorted(set(evidence)))
    return Recommendation.model_validate(
        dict(
            recommendation_id="rec-" + category,
            priority=priority,
            category=category,
            title=title,
            action=action,
            affected_checks=len(ids),
            check_ids=ids[:128],
            evidence_ids=refs[:128],
            references_truncated=len(ids) > 128 or len(refs) > 128,
        )
    )


def _step1_items(result: Step1Result) -> tuple[Recommendation, ...]:
    items = []
    for kind, category, priority, title, action in RULES:
        selected = [
            c
            for c in result.checks.checks
            if c.kind == kind and c.status in {"fail", "warning", "unknown"}
        ]
        if selected:
            items.append(
                _item(
                    category,
                    priority,
                    title,
                    action,
                    tuple(c.check_id for c in selected),
                    tuple(i for c in selected for i in c.evidence_ids),
                )
            )
    if not result.crawl.material.can_start_step2:
        items.append(
            _item(
                "material",
                "high",
                "Provide a concrete business description",
                (
                    "Provide authored text describing activities and products/services. "
                    "Length alone does not establish sufficient business material; "
                    "technical failures remain unchanged."
                ),
            )
        )
    if result.run.limitations:
        items.append(
            _item(
                "coverage",
                "medium",
                "Review incomplete coverage",
                (
                    "Read the run limitations, terminal candidate states and evidence. "
                    "Start a new explicit audit if the network handoff expired; never reset"
                    " its timer."
                ),
            )
        )
    return tuple(items)


def recommendation_report(directory: Path) -> RecommendationReport:
    "Validate saved step0 or step1, then derive bounded recommendations without HTTP/DNS."
    if (directory / "run.json").exists():
        result, committed = _load_step1_result(directory)
        step1_items = _step1_items(result)
        known_checks = {c.check_id for c in result.checks.checks}
        known_evidence = {e.evidence_id for e in result.evidence}
        if any(
            not set(r.check_ids) <= known_checks or not set(r.evidence_ids) <= known_evidence
            for r in step1_items
        ):
            raise ValueError("recommendation has dangling source reference")
        return RecommendationReport(
            source_step=1,
            source_run_id=result.source.run_id,
            source_publication_id=result.run.publication_id,
            source_commit_sha256=digest(committed),
            observed_at=result.run.finished_at,
            recommendations=step1_items,
            limitations=(
                (
                    "Technical observations only; not measured GEO visibility, ranking or "
                    "recommendation by an AI system."
                ),
                "Offline report describes the source observation time, "
                "not a new live verification.",
                *result.run.limitations,
            ),
        )
    site = load_site_result(directory)
    data = EvidenceStore(directory).read_bytes("site.json", limit=MAX_JSON_BYTES)
    if SiteValidationResult.model_validate_json(data) != site:
        raise problem("INPUT_ARTIFACT_INVALID", "input", "Source changed during report creation.")
    evidence = tuple(e.evidence_id for e in site.evidence)
    items = []
    if any(e.code.startswith("HTTPS_") for e in site.errors):
        items.append(
            _item(
                "https",
                "high",
                "Review recorded TLS failure",
                (
                    "Check the recorded certificate/connection failure. HTTP fallback does "
                    "not erase TLS failure; keep verification enabled."
                ),
                evidence=evidence,
            )
        )
    if not site.can_enter_step1:
        items.append(
            _item(
                "access",
                "high",
                "Resolve the step0 entry barrier",
                (
                    "Review reason_codes and recorded HTTP/target policy. Use a permitted "
                    "public origin or provide public material; do not bypass "
                    "private-target, login or CAPTCHA controls."
                ),
                evidence=evidence,
            )
        )
    items.append(
        _item(
            "coverage",
            "info",
            "Full technical crawl was not observed",
            (
                "This source contains step0 only. Run a new explicit audit for complete"
                " step1; an expired handoff must not be refreshed silently."
            ),
        )
    )
    return RecommendationReport(
        source_step=0,
        source_run_id=site.run_id,
        source_publication_id=site.run_id,
        source_commit_sha256=digest(data),
        observed_at=site.finished_at,
        recommendations=tuple(items),
        limitations=(
            "Step0 observations only; no full crawl, business profile or measured AI visibility.",
        ),
    )


def validate_recommendations(report: RecommendationReport, directory: Path) -> RecommendationReport:
    expected = recommendation_report(directory)
    if report != expected:
        raise problem(
            "INPUT_ARTIFACT_INVALID",
            "input",
            "Recommendations do not match verified source and rule version.",
        )
    return expected
