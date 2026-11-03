"""Replay step 8 once, then compute auditable question-weighted statistics."""

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..contracts import Contract
from ..evidence import EvidenceStore
from ..step5.models import Answer, MechanicalAnswer, PromptSet, Target
from ..step5.pipeline import MAX_METADATA_BYTES, digest, encoded, unique_json
from ..step5.replay import replay_session, reuse_replay
from ..step6.models import Annotation
from ..step7.models import ResolvedAnnotation, Step7Summary
from ..step8.models import BrandMap, BrandOccurrence, DomainTypes
from ..step8.models import PublicationManifest as Step8Manifest
from ..step8.pipeline import INPUT_FILES as STEP8_INPUTS
from ..step8.pipeline import build_artifacts as build_step8
from ..step8.pipeline import capture_inputs
from ..step8.pipeline import verify_publication as verify_step8
from . import storage
from .context import load_context
from .models import (
    Aggregates,
    AnswerMeasure,
    Cohort,
    Competitor,
    MetricPolicy,
    NumberCell,
    SiteContext,
    SourceInspection,
    SourcePageTask,
    SourceStat,
)
from .statistics import questions, rates, stability_counts

INPUT_FILES = (
    *STEP8_INPUTS,
    "step8-manifest.json",
    "site-context.json",
    "metric-policy.json",
    "external-pages.json",
)


def json_model[T: Contract](data: bytes, model: type[T]) -> T:
    unique_json(data)
    return model.model_validate_json(data)


def decode_rows[T: Contract](data: bytes, model: type[T]) -> list[T]:
    return [json_model(line, model) for line in data.splitlines()]


def number_registry(aggregate: Aggregates) -> dict[str, NumberCell]:
    numbers = {
        name: NumberCell(value=value, unit="count", basis=name)
        for name, value in (
            ("total_answers", aggregate.total_answers),
            ("eligible_answers", aggregate.eligible_answers),
            ("excluded_answers", aggregate.excluded_answers),
            ("organic_questions", sum(q.organic for q in aggregate.questions)),
            ("answered_questions", sum(q.valid_answers > 0 for q in aggregate.questions)),
        )
    }
    for rate in aggregate.rates:
        for suffix, value in (
            ("", rate.percent),
            ("-low", rate.interval_low),
            ("-high", rate.interval_high),
        ):
            if value is not None:
                numbers[rate.metric_id + suffix] = NumberCell(
                    value=value, unit="percent", basis=rate.scope + ":" + rate.metric
                )
        for suffix, count in (
            ("-questions", rate.questions),
            ("-answered", rate.answered_questions),
            ("-unknown", rate.unknown_answers),
            ("-known", rate.known_answers),
        ):
            numbers[rate.metric_id + suffix] = NumberCell(
                value=count, unit="count", basis=rate.scope
            )
    for key, value in aggregate.stability.items():
        numbers["stability-" + key] = NumberCell(value=value, unit="count", basis="question_cohort")
    for key, value in aggregate.technical_counts.items():
        numbers["technical-" + key] = NumberCell(
            value=value, unit="count", basis="verified_site_checks"
        )
    for entity in aggregate.competitors:
        numbers[entity.entity_id + "-answers"] = NumberCell(
            value=len(entity.answer_ids), unit="count", basis="distinct_eligible_answers"
        )
    for source in aggregate.sources:
        numbers["host-" + digest(source.host.encode())[:24]] = NumberCell(
            value=len(source.answer_ids), unit="count", basis="distinct_eligible_citing_answers"
        )
    if aggregate.source_inspection:
        numbers["source-pages-observed"] = NumberCell(
            value=sum(
                item.status == "observed" for item in aggregate.source_inspection.observations
            ),
            unit="count",
            basis="explicit_external_page_inspection",
        )
    return numbers


def _build_artifacts(inputs: dict[str, bytes]) -> dict[str, bytes]:
    if set(inputs) != set(INPUT_FILES):
        raise ValueError("unexpected step 9 inputs")
    storage.bounded(inputs)
    previous = {name: inputs[name] for name in STEP8_INPUTS}
    manifest = Step8Manifest.model_validate(unique_json(inputs["step8-manifest.json"]))
    outputs8, _ = build_step8(previous)
    if manifest.source_fingerprint != storage.fingerprint(previous) or set(manifest.files) != set(
        outputs8
    ):
        raise ValueError("step 8 captured manifest differs from replay")
    for name, data in outputs8.items():
        if manifest.files[name].sha256 != digest(data) or manifest.files[name].size_bytes != len(
            data
        ):
            raise ValueError("step 8 captured content differs from replay")
    prompts = json_model(inputs["prompts.json"], PromptSet)
    target = json_model(inputs["target.json"], Target)
    context = json_model(inputs["site-context.json"], SiteContext)
    policy = json_model(inputs["metric-policy.json"], MetricPolicy)
    primary = decode_rows(inputs["annotations.jsonl"], Annotation)
    effective = {
        a.answer_id: a
        for a in decode_rows(inputs["resolved_annotations.jsonl"], ResolvedAnnotation)
    }
    mechanical = {a.answer_id: a for a in decode_rows(inputs["mechanical.jsonl"], MechanicalAnswer)}
    occurrences = decode_rows(outputs8["brand_occurrences.jsonl"], BrandOccurrence)
    brand_map = json_model(outputs8["brand_map.json"], BrandMap)
    domain_types = json_model(outputs8["domain_types.json"], DomainTypes)
    from ..step7.pipeline import INPUT_FILES as STEP7_INPUTS
    from ..step7.pipeline import build_artifacts as build_step7

    outputs7, _ = build_step7({name: inputs[name] for name in STEP7_INPUTS})
    review = json_model(outputs7["summary.json"], Step7Summary)
    original_lines = inputs["answers.jsonl"].splitlines()
    prompt_map = {p.prompt_id: p for p in prompts.prompts}
    recommended_other = {
        o.answer_id
        for o in occurrences
        if o.measurement_eligible and o.is_target == "no" and o.recommended == "yes"
    }
    rows = []
    cohorts: dict[str, dict[str, Any]] = {}
    for annotation in primary:
        original = json_model(original_lines[annotation.source_line - 1], Answer)
        resolved = effective[annotation.answer_id]
        observed = mechanical[annotation.answer_id]
        prompt = prompt_map[annotation.prompt_id]
        cohort = dict(provider=original.provider, model=original.model, settings=original.settings)
        cohort_id = "cohort-" + digest(encoded(cohort))[:24]
        cohorts[cohort_id] = cohort
        excluded = []
        if original.status != "ok":
            excluded.append("delivery_" + original.status)
        if not prompt.active or not prompt.visibility_eligible:
            excluded.append("not_organic_question")
        if not original.collection_eligible:
            excluded.append("system_excluded_by_collector")
        if not resolved.measurement_eligible and not excluded:
            excluded.append("not_eligible_upstream")
        rows.append(
            AnswerMeasure(
                answer_id=annotation.answer_id,
                prompt_id=annotation.prompt_id,
                group=annotation.group,
                cohort_id=cohort_id,
                provider=original.provider,
                model=original.model,
                synthetic=original.synthetic or original.origin == "synthetic",
                status=original.status,
                eligible=not excluded,
                excluded_reasons=excluded,
                name_match=("yes" if observed.target_name_candidate else "no")
                if original.status == "ok"
                else None,
                presence=resolved.target_presence,
                recommended=resolved.target_recommended,
                discouraged=resolved.target_discouraged,
                cited=resolved.target_cited,
                other_recommended=annotation.answer_id in recommended_other,
            )
        )
    valid_ids = {r.answer_id for r in rows if r.eligible}
    by_entity: dict[str, list[BrandOccurrence]] = defaultdict(list)
    for occurrence in occurrences:
        if occurrence.answer_id in valid_ids:
            by_entity[occurrence.entity_id].append(occurrence)
    competitors = []
    for entity in brand_map.entities:
        if entity.identity_scope == "target":
            continue
        members = by_entity[entity.entity_id]
        ids = sorted({m.answer_id for m in members})
        recommended = sorted(
            {m.answer_id for m in members if m.recommended == "yes" and m.is_target == "no"}
        )
        competitors.append(
            Competitor(
                entity_id=entity.entity_id,
                name=entity.canonical_name,
                identity_scope=entity.identity_scope,
                answer_ids=ids,
                recommended_answer_ids=recommended,
                share_percent=100.0 * len(ids) / len(valid_ids) if valid_ids else None,
                candidate_only=True,
            )
        )
    sources = [
        SourceStat(
            host=d.host,
            kind=d.kind,
            owner=d.owner,
            answer_ids=sorted(set(d.eligible_citation_answer_ids) & valid_ids),
            urls=d.urls,
        )
        for d in domain_types.domains
    ]
    page_answers: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.eligible and row.group in {"C", "D"}:
            for source in mechanical[row.answer_id].sources:
                if source.citation_indices and not source.target_host_match:
                    page_answers[source.url].add(row.answer_id)
    page_tasks = [
        SourcePageTask(
            task_id="source-" + digest(url.encode())[:24], url=url, answer_ids=sorted(ids)
        )
        for url, ids in sorted(page_answers.items(), key=lambda pair: (-len(pair[1]), pair[0]))[
            : policy.max_source_pages
        ]
    ]
    warnings = [
        "Доли относятся к собранным вопросам, а не ко всем будущим ответам модели.",
        "Интервал — описательный разброс повторной выборки вопросов; повторы могут быть зависимы.",
        "Сторонние имена являются кандидатами; их существование и полнота списка не проверены.",
    ]
    if review.independent_review == "not_performed":
        warnings.append("Независимая модельная проверка не выполнена.")
    if any(r.eligible and r.presence == "uncertain" for r in rows):
        warnings.append("Неясная идентичность не засчитана в подтверждённые упоминания.")
    if any(r.eligible and r.recommended == "uncertain" for r in rows):
        warnings.append(
            "Неразобранные рекомендации исключены из их знаменателя; показано число пропусков."
        )
    external = (
        json_model(inputs["external-pages.json"], SourceInspection)
        if unique_json(inputs["external-pages.json"]) is not None
        else None
    )
    if page_tasks and external is None:
        warnings.append(
            "Страницы внешних источников не посещались: требуется отдельное явное действие."
        )
    warnings.extend(context.limitations)
    role = review.metrics["target_roles"].reliability
    aggregate = Aggregates(
        target=target,
        synthetic=target.synthetic or any(r.synthetic for r in rows),
        policy=policy,
        total_answers=len(rows),
        eligible_answers=len(valid_ids),
        excluded_answers=len(rows) - len(valid_ids),
        providers=dict(Counter(r.provider for r in rows)),
        cohorts={key: Cohort.model_validate(value) for key, value in cohorts.items()},
        delivery_statuses=dict(Counter(r.status for r in rows)),
        exclusion_reasons=dict(Counter(reason for r in rows for reason in r.excluded_reasons)),
        questions=questions(prompts, rows),
        rates=rates(prompts, rows, policy, role, review.metrics["identity"].reliability),
        numbers={},
        competitors=competitors,
        sources=sources,
        source_page_tasks=page_tasks,
        review_metrics=review.metrics,
        stability=stability_counts(rows),
        technical_counts=dict(Counter(c.status for c in context.checks)),
        warnings=warnings,
    )
    aggregate = aggregate.model_copy(update={"numbers": number_registry(aggregate)})
    if external:
        from .sources import validate_export

        validate_export(external, aggregate)
        aggregate = aggregate.model_copy(update={"source_inspection": external})
        aggregate = aggregate.model_copy(update={"numbers": number_registry(aggregate)})
    return storage.artifacts(
        inputs,
        {
            "aggregates.json": encoded(aggregate.model_dump(mode="json")),
            "answer_metrics.jsonl": b"".join(encoded(r.model_dump(mode="json")) for r in rows),
            "cohorts.json": encoded(cohorts),
            "context.json": inputs["site-context.json"],
            "source_page_jobs.jsonl": b"".join(
                encoded(p.model_dump(mode="json")) for p in page_tasks
            ),
            "validation.json": encoded(
                {
                    "totals_reconciled": True,
                    "source_replayed": True,
                    "means_weight_questions_equally": True,
                    "bootstrap_unit": "question",
                    "network_requests": 0,
                    "model_calls": 0,
                }
            ),
        },
    )


@reuse_replay
def cached_artifacts(inputs: dict[str, bytes]) -> tuple[dict[str, bytes], MetricPolicy]:
    return _build_artifacts(inputs), json_model(inputs["metric-policy.json"], MetricPolicy)


def build_artifacts(inputs: dict[str, bytes]) -> dict[str, bytes]:
    return cached_artifacts(inputs)[0]


def run_step9(
    step8_dir: Path,
    output_dir: Path,
    *,
    site_dir: Path | None = None,
    policy: MetricPolicy | None = None,
    source_checks_dir: Path | None = None,
) -> Aggregates:
    storage.separate(step8_dir, output_dir)
    if site_dir:
        storage.separate(site_dir, output_dir)
    external = b"null\n"
    if source_checks_dir:
        from .sources import verify_inspection

        storage.separate(source_checks_dir, output_dir)
        external = encoded(verify_inspection(source_checks_dir).model_dump(mode="json"))
    source = EvidenceStore(step8_dir)
    with replay_session():
        verify_step8(step8_dir)
        inputs = capture_inputs(source, published=True)
        target = json_model(inputs["target.json"], Target)
        inputs.update(
            {
                "step8-manifest.json": source.read_bytes("manifest.json", limit=MAX_METADATA_BYTES),
                "site-context.json": encoded(
                    load_context(site_dir, target).model_dump(mode="json")
                ),
                "metric-policy.json": encoded((policy or MetricPolicy()).model_dump(mode="json")),
                "external-pages.json": external,
            }
        )
        files = storage.publish(inputs, output_dir, 9, build_artifacts)
    return json_model(files["aggregates.json"], Aggregates)


def verify_publication(directory: Path) -> Aggregates:
    with replay_session():
        files = storage.verify(directory, 9, INPUT_FILES, build_artifacts)
    return json_model(files["aggregates.json"], Aggregates)
