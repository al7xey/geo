"""Macro means and deterministic question-cluster resampling; stdlib only."""

import random
from collections import Counter, defaultdict
from statistics import fmean
from typing import Literal

from ..step5.models import PromptSet
from ..step5.pipeline import digest
from ..step6.models import Decision
from .models import AnswerMeasure, MetricName, MetricPolicy, QuestionMetric, QuestionResult, Rate

METRICS: tuple[MetricName, ...] = ("name_match", "presence", "recommended", "discouraged", "cited")


def question_metric(rows: list[AnswerMeasure], name: MetricName) -> QuestionMetric:
    values: list[Decision | None] = [getattr(row, name) for row in rows]
    unknown = sum(value not in {"yes", "no"} for value in values)
    # Identity is conservative: unresolved identity remains in the presence
    # denominator. Unparsed semantic roles are excluded and reported separately.
    denominator = len(values) if name == "presence" else len(values) - unknown
    yes = values.count("yes")
    return QuestionMetric(
        metric=name,
        successes=yes,
        denominator=denominator,
        unknown=unknown,
        percent=100.0 * yes / denominator if denominator else None,
    )


def stability(
    rows: list[AnswerMeasure],
) -> Literal["always", "sometimes", "never", "uncertain", "no_data"]:
    if not rows:
        return "no_data"
    values = {row.presence for row in rows}
    if values == {"yes"}:
        return "always"
    if values == {"no"}:
        return "never"
    if "yes" in values and "no" in values:
        return "sometimes"
    return "uncertain"


def questions(prompts: PromptSet, rows: list[AnswerMeasure]) -> list[QuestionResult]:
    grouped: dict[str, list[AnswerMeasure]] = defaultdict(list)
    for row in rows:
        if row.eligible:
            grouped[row.prompt_id].append(row)
    return [
        QuestionResult(
            prompt_id=p.prompt_id,
            text=p.text,
            group=p.group,
            organic=p.active and p.visibility_eligible,
            valid_answers=len(grouped[p.prompt_id]),
            target_confirmed=sum(r.presence == "yes" for r in grouped[p.prompt_id]),
            target_uncertain=sum(r.presence == "uncertain" for r in grouped[p.prompt_id]),
            name_matches=sum(r.name_match == "yes" for r in grouped[p.prompt_id]),
            other_recommended_answers=sum(r.other_recommended for r in grouped[p.prompt_id]),
            other_recommended_percent=(
                100.0
                * sum(r.other_recommended for r in grouped[p.prompt_id])
                / len(grouped[p.prompt_id])
            )
            if grouped[p.prompt_id]
            else None,
            metrics=[question_metric(grouped[p.prompt_id], name) for name in METRICS],
            stability=stability(grouped[p.prompt_id]),
        )
        for p in prompts.prompts
    ]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def rates(
    prompts: PromptSet,
    rows: list[AnswerMeasure],
    policy: MetricPolicy,
    role_reliability: str,
    identity_reliability: str = "not_assessed",
) -> list[Rate]:
    eligible = [row for row in rows if row.eligible]
    cohort_ids = sorted({row.cohort_id for row in rows})
    if len(cohort_ids) > policy.max_cohorts:
        raise ValueError("cohort budget exceeded")
    scopes: list[tuple[str | None, Literal["A", "B", "C", "D"] | None]] = [(None, None)]
    groups: tuple[Literal["A", "B", "C", "D"], ...] = ("A", "B", "C", "D")
    scopes.extend((None, group) for group in groups)
    for cohort_id in cohort_ids:
        scopes.append((cohort_id, None))
        scopes.extend((cohort_id, group) for group in groups)
    result = []
    for cohort, group in scopes:
        selected = [
            r
            for r in eligible
            if (cohort is None or r.cohort_id == cohort) and (group is None or r.group == group)
        ]
        matrix: dict[tuple[str, str], list[AnswerMeasure]] = defaultdict(list)
        for row in selected:
            matrix[(row.prompt_id, row.cohort_id)].append(row)
        wanted = [
            p
            for p in prompts.prompts
            if p.active and p.visibility_eligible and (group is None or p.group == group)
        ]
        scope = (cohort or "all") + "-" + (group or "all")
        for name in METRICS:
            clusters: dict[str, list[float]] = defaultdict(list)
            unknown = 0
            for (qid, _), members in sorted(matrix.items()):
                q = question_metric(members, name)
                unknown += q.unknown
                if q.percent is not None:
                    clusters[qid].append(q.percent)
            # Each question has equal weight; within it each available cohort
            # has equal weight. Extra adaptive repeats do not weight a question.
            values = [fmean(clusters[p.prompt_id]) for p in wanted if clusters[p.prompt_id]]
            metric_id = "m-" + digest((scope + ":" + name).encode())[:24]
            low = high = None
            if len(values) >= 2:
                # Reproducible statistical resampling, never security randomness.
                rng = random.Random(policy.seed ^ int(digest(metric_id.encode())[:8], 16))  # nosec B311
                samples = [
                    fmean(rng.choices(values, k=len(values)))
                    for _ in range(policy.bootstrap_replicates)
                ]
                low, high = percentile(samples, 0.025), percentile(samples, 0.975)
            reliability: Literal["not_assessed", "insufficient_sample", "medium", "low"]
            if not values:
                reliability = "not_assessed"
            elif len(values) < 5:
                reliability = "insufficient_sample"
            elif unknown * 10 > len(selected):
                reliability = "low"
            elif name in {"presence", "recommended", "discouraged"}:
                assessment = identity_reliability if name == "presence" else role_reliability
                reliability = (
                    "medium"
                    if assessment == "medium"
                    else ("low" if assessment == "low" else "not_assessed")
                )
            else:
                reliability = "medium"
            result.append(
                Rate(
                    metric_id=metric_id,
                    metric=name,
                    scope=scope,
                    cohort_id=cohort,
                    group=group,
                    percent=fmean(values) if values else None,
                    interval_low=low,
                    interval_high=high,
                    questions=len(wanted),
                    answered_questions=len(values),
                    known_answers=len(selected) - unknown,
                    unknown_answers=unknown,
                    reliability=reliability,
                )
            )
    return result


def stability_counts(rows: list[AnswerMeasure]) -> dict[str, int]:
    groups: dict[tuple[str, str], list[AnswerMeasure]] = defaultdict(list)
    for row in rows:
        if row.eligible:
            groups[(row.prompt_id, row.cohort_id)].append(row)
    counts = Counter[str](stability(group) for group in groups.values())
    return {name: counts[name] for name in ("always", "sometimes", "never", "uncertain")}
