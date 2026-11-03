"""Agreement is measured on the random sample; only unknown primary fields resolve."""

from typing import Literal

from ..step5.extract import folded
from ..step5.pipeline import digest, encoded
from ..step6.models import Annotation, BrandAnnotation
from .models import (
    AgreementMetric,
    ResolvedAnnotation,
    ReviewedBrand,
    ReviewPolicy,
    ReviewResult,
    SelectedAnswer,
)

TARGET_FIELDS = ("target_presence", "target_recommended", "target_discouraged")


def matching_brand(primary: BrandAnnotation, result: ReviewResult) -> ReviewedBrand | None:
    if primary.is_target == "yes":
        return next((b for b in result.brands if b.is_target == "yes"), None)
    names = {folded(primary.name)[0], *(folded(e.quote)[0] for e in primary.presence_evidence)}
    matches = [b for b in result.brands if folded(b.name)[0] in names]
    return matches[0] if len(matches) == 1 else None


def definite(value: object) -> bool:
    return value in ("yes", "no")


def metric(
    expected: int,
    pairs: list[tuple[object, object]],
    policy: ReviewPolicy,
    *,
    comparable_answers: int | None = None,
) -> AgreementMetric:
    comparable = len(pairs)
    matches = sum(left == right for left, right in pairs)
    agreement = 100 * matches / comparable if comparable else None
    coverage = comparable / expected if expected else None
    answer_count = comparable if comparable_answers is None else comparable_answers
    reliability: Literal["not_assessed", "insufficient_sample", "medium", "low"]
    if not comparable:
        reliability = "not_assessed"
    elif (
        comparable < policy.minimum_comparable
        or answer_count < (policy.minimum_distinct_answers or policy.minimum_comparable)
        or coverage is None
        or coverage < policy.minimum_comparison_coverage
    ):
        reliability = "insufficient_sample"
    else:
        reliability = (
            "medium" if 100 * matches >= policy.agreement_threshold_percent * comparable else "low"
        )
    return AgreementMetric(
        assigned_comparisons=expected,
        comparable=comparable,
        comparable_answers=answer_count,
        matches=matches,
        disagreements=comparable - matches,
        comparison_coverage=coverage,
        agreement_percent=agreement,
        reliability=reliability,
    )


def agreements(
    annotations: list[Annotation],
    selection: list[SelectedAnswer],
    results: dict[str, ReviewResult],
    policy: ReviewPolicy,
) -> dict[str, AgreementMetric]:
    primary = {a.answer_id: a for a in annotations}
    expected = {"identity": 0, "target_roles": 0, "competitor_roles": 0}
    pairs: dict[str, list[tuple[object, object]]] = {key: [] for key in expected}
    answer_ids: dict[str, set[str]] = {key: set() for key in expected}
    for item in selection:
        if not item.random_sample:
            continue
        a = primary[item.answer_id]
        imported = results.get(item.job_id)
        review = (
            imported
            if imported and imported.status == "ok" and imported.reviewer.kind == "model"
            else None
        )
        if definite(a.target_presence):
            expected["identity"] += 1
            if review and definite(review.target_presence):
                pairs["identity"].append((a.target_presence, review.target_presence))
                answer_ids["identity"].add(a.answer_id)
        if (
            definite(a.target_presence)
            and definite(a.target_recommended)
            and definite(a.target_discouraged)
        ):
            expected["target_roles"] += 1
            if (
                review
                and definite(review.target_presence)
                and definite(review.target_recommended)
                and definite(review.target_discouraged)
            ):
                pairs["target_roles"].append(
                    (
                        (a.target_presence, a.target_recommended, a.target_discouraged),
                        (
                            review.target_presence,
                            review.target_recommended,
                            review.target_discouraged,
                        ),
                    )
                )
                answer_ids["target_roles"].add(a.answer_id)
        for brand in a.brands:
            if (
                brand.is_target != "no"
                or not definite(brand.recommended)
                or not definite(brand.discouraged)
            ):
                continue
            expected["competitor_roles"] += 1
            other = matching_brand(brand, review) if review else None
            if (
                other
                and other.is_target == "no"
                and definite(other.recommended)
                and definite(other.discouraged)
            ):
                pairs["competitor_roles"].append(
                    ((brand.recommended, brand.discouraged), (other.recommended, other.discouraged))
                )
                answer_ids["competitor_roles"].add(a.answer_id)
    return {
        name: metric(expected[name], pairs[name], policy, comparable_answers=len(answer_ids[name]))
        for name in expected
    }


def resolve(
    annotations: list[Annotation], selection: list[SelectedAnswer], results: dict[str, ReviewResult]
) -> tuple[list[dict[str, object]], int]:
    selected = {item.answer_id: item for item in selection}
    rows = []
    resolved_fields = 0
    for a in annotations:
        item = selected.get(a.answer_id)
        imported = results.get(item.job_id) if item else None
        review = (
            imported
            if imported
            and imported.status == "ok"
            and imported.reviewer.kind in {"model", "manual"}
            else None
        )
        values = {field: getattr(a, field) for field in TARGET_FIELDS}
        sources = {field: "primary_rules" for field in TARGET_FIELDS}
        conflicts = []
        brand_conflicts: list[dict[str, object]] = []
        brand_overrides: list[dict[str, object]] = []
        if review and item and item.uncertain_case:
            for field in TARGET_FIELDS:
                reviewed = getattr(review, field)
                if values[field] == "uncertain" and definite(reviewed):
                    if (
                        field != "target_presence"
                        and values["target_presence"] != review.target_presence
                    ):
                        continue
                    values[field] = reviewed
                    sources[field] = "imported_" + review.reviewer.kind
                    resolved_fields += 1
            for index, brand in enumerate(a.brands):
                other = matching_brand(brand, review)
                if other is None:
                    continue
                changed: dict[str, object] = {}
                for field in ("is_target", "recommended", "discouraged"):
                    # Roles cannot be attributed while the reviewed identity is still unclear.
                    if field != "is_target" and not definite(other.is_target):
                        continue
                    if getattr(brand, field) == "uncertain" and definite(getattr(other, field)):
                        changed[field] = getattr(other, field)
                # Never silently change an identity that was already confidently different.
                if brand.is_target != "uncertain" and other.is_target != brand.is_target:
                    changed = {}
                if changed:
                    brand_overrides.append(
                        dict(
                            primary_brand_index=index,
                            reviewed_name=other.name,
                            changes=changed,
                            review_job_id=review.job_id,
                        )
                    )
                    resolved_fields += len(changed)
        if review:
            for field in TARGET_FIELDS:
                if (
                    definite(getattr(a, field))
                    and definite(getattr(review, field))
                    and getattr(a, field) != getattr(review, field)
                ):
                    conflicts.append(field)
            for index, brand in enumerate(a.brands):
                other = matching_brand(brand, review)
                if not other:
                    continue
                fields = [
                    field
                    for field in ("is_target", "recommended", "discouraged")
                    if definite(getattr(brand, field))
                    and definite(getattr(other, field))
                    and getattr(brand, field) != getattr(other, field)
                    and (field == "is_target" or brand.is_target == other.is_target)
                ]
                if fields:
                    brand_conflicts.append(
                        dict(
                            primary_brand_index=index,
                            reviewed_name=other.name,
                            fields=fields,
                            review_job_id=review.job_id,
                        )
                    )
        # Any unresolved ranks remain explicitly outside this two-field check.
        outstanding = [field for field, value in values.items() if value == "uncertain"]
        for index, brand in enumerate(a.brands):
            override = next(
                (b["changes"] for b in brand_overrides if b["primary_brand_index"] == index), {}
            )
            for field in ("is_target", "recommended", "discouraged"):
                value = (
                    override.get(field, getattr(brand, field))
                    if isinstance(override, dict)
                    else getattr(brand, field)
                )
                if value == "uncertain":
                    outstanding.append(f"brands/{index}/{field}")
            if "conflicting_ranks" in brand.reason_codes:
                outstanding.append(f"brands/{index}/rank_outside_review_scope")
        rows.append(
            ResolvedAnnotation.model_validate(
                dict(
                    answer_id=a.answer_id,
                    primary_text_sha256=a.text_sha256,
                    primary_annotation_sha256=digest(encoded(a.model_dump(mode="json"))),
                    **values,
                    target_cited=a.target_cited,
                    measurement_eligible=a.measurement_eligible,
                    field_sources=sources,
                    brand_overrides=brand_overrides,
                    brand_disagreements=brand_conflicts,
                    outstanding_fields=outstanding,
                    confident_disagreements=conflicts,
                    review_job_id=item.job_id if item else None,
                    review_state=(
                        "imported_" + review.reviewer.kind
                        if review
                        else "pending"
                        if item
                        else "not_selected"
                    ),
                )
            ).model_dump(mode="json")
        )
    return rows, resolved_fields
