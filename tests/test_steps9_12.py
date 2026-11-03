"""Final offline chain: arithmetic, missingness, evidence, drafts and safe HTML."""

import json
import socket

import pytest
from pydantic import ValidationError

from geo_audit.errors import GeoAuditError
from geo_audit.step5.pipeline import digest, encoded
from geo_audit.step5.workflow import run as early_tail
from geo_audit.step9 import pipeline as s9
from geo_audit.step9 import sources
from geo_audit.step9.models import (
    Aggregates,
    AnswerMeasure,
    CompanyFact,
    MetricPolicy,
    PageContext,
    SiteContext,
    SourceInspection,
)
from geo_audit.step9.statistics import question_metric, rates, stability
from geo_audit.step10 import pipeline as s10
from geo_audit.step10.models import GapResponse, Gaps, GapSuggestion
from geo_audit.step11.models import Plan
from geo_audit.step11.pipeline import PERIODS
from geo_audit.step12 import workflow
from geo_audit.step12.report import audit_html
from tests.test_step4_to_step8 import fake_api  # noqa: F401 - controlled API fixture


@pytest.fixture(scope="module")
def early(tmp_path_factory):
    root = tmp_path_factory.mktemp("final-chain")
    approved = root / "approved"
    approved.mkdir()
    collected = root / "collected"
    collected.mkdir()
    prompts = [
        dict(
            id=f"q{i}",
            text="Сравни Факел" if i == 4 else "Выбор компании для проекта " + str(i),
            level=min(i, 4),
            origin="suggested",
            has_brand=i == 4,
            counts_in_organic_visibility=i != 4,
        )
        for i in range(1, 6)
    ]
    doc = dict(
        schema_version="step4-input-1",
        domain="fakel.example.test",
        country="Россия",
        language="русский",
        name_variants=["Факел", "FKL"],
        prompts=prompts,
        prompts_sha256=digest(
            json.dumps(prompts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ),
    )
    (approved / "step4_input.json").write_bytes(encoded(doc))
    (approved / "profile.json").write_bytes(
        encoded(
            dict(
                domain=doc["domain"],
                name_variants=[
                    dict(variant="Факел", ambiguous=False),
                    dict(variant="FKL", ambiguous=True),
                ],
            )
        )
    )
    answers = []
    for q, count, text in [
        (1, 4, "Рекомендую Факел для этой задачи."),
        (2, 1, "Рекомендованные компании:\n1. Орион — вариант для этого проекта."),
        (3, 1, "FKL — возможный вариант."),
        (4, 1, "Рекомендую Факел."),
    ]:
        for repeat in range(1, count + 1):
            answers.append(
                dict(
                    answer_id=f"a-{q}-{repeat}",
                    prompt_id=f"q{q}",
                    system="mock-one",
                    model="fixture-v1",
                    repeat=repeat,
                    status="ok",
                    text=text,
                    sources=["https://directory.example.test/choice"] if q == 3 else [],
                )
            )
    for repeat in range(1, 3):
        answers.append(
            dict(
                answer_id=f"bad-{repeat}",
                prompt_id="q5",
                system="mock-failed",
                model="fixture-v1",
                repeat=repeat,
                status="error",
                text="",
                sources=[],
            )
        )
    (collected / "answers.jsonl").write_bytes(b"".join(encoded(a) for a in answers))
    output = root / "early"
    early_tail(approved / "step4_input.json", collected, output, synthetic=True)
    return output


@pytest.fixture(scope="module")
def final(early, tmp_path_factory):
    output = tmp_path_factory.mktemp("final-parent") / "tail"
    workflow.run(
        early / "step8", output, upstream_chain=early, policy=MetricPolicy(bootstrap_replicates=100)
    )
    return output


def test_question_weights_missingness_and_exclusions(final):
    aggregate = Aggregates.model_validate_json((final / "aggregates.json").read_bytes())
    assert (aggregate.total_answers, aggregate.eligible_answers, aggregate.excluded_answers) == (
        9,
        6,
        3,
    )
    assert sum(aggregate.providers.values()) == aggregate.total_answers
    result = {q.prompt_id: q for q in aggregate.questions}
    assert result["q5"].valid_answers == 0 and result["q5"].stability == "no_data"
    assert all(m.percent is None for m in result["q5"].metrics)
    assert result["q3"].target_uncertain == 1
    rate = next(r for r in aggregate.rates if r.scope == "all-all" and r.metric == "presence")
    assert rate.percent == pytest.approx(100 / 3)
    assert rate.unknown_answers == 1
    assert aggregate.model_calls == aggregate.network_requests == 0 and aggregate.synthetic


def test_gap_selection_is_evidence_based_and_not_invented(final):
    gaps = Gaps.model_validate_json((final / "step10/gaps.json").read_bytes())
    assert gaps.candidate_question_ids == ["q2"]
    assert len(gaps.topics) == 1 and gaps.topics[0].material is None
    job = json.loads((final / "step10/gap_jobs.jsonl").read_bytes())
    assert job["status"] == "needs_site_context" and job["executed"] is False
    assert job["payload_chars"] <= 24000 and job["max_output_tokens"] == 1600
    assert "annotations" not in job["payload"]


def test_plan_order_and_final_report_numeric_provenance(final):
    plan = Plan.model_validate_json((final / "plan.json").read_bytes())
    assert all(a.basis for a in plan.actions)
    assert plan.actions == sorted(
        plan.actions, key=lambda a: (PERIODS[a.period], -len(a.question_ids), a.action_id)
    )
    aggregate = Aggregates.model_validate_json((final / "aggregates.json").read_bytes())
    html = (final / "report.html").read_text("utf-8")
    assert audit_html(html, aggregate)
    assert "Учебные данные" in html and "Нет данных" in html
    assert "requested_fields" not in html and "<script" not in html
    assert workflow.verify(final).committed


def test_bootstrap_reproducible_and_not_answer_weighted(final):
    aggregate = Aggregates.model_validate_json((final / "aggregates.json").read_bytes())
    source = final / "step9/input/prompts.json"
    from geo_audit.step5.models import PromptSet

    prompts = PromptSet.model_validate_json(source.read_bytes())
    rows = s9.decode_rows((final / "step9/answer_metrics.jsonl").read_bytes(), AnswerMeasure)
    first = rates(prompts, rows, aggregate.policy, "not_assessed")
    assert first == rates(prompts, rows, aggregate.policy, "not_assessed")
    selected = [r for r in rows if r.eligible and r.prompt_id in {"q1", "q2"}]
    rate = next(
        r
        for r in rates(prompts, selected, aggregate.policy, "not_assessed")
        if r.scope == "all-all" and r.metric == "presence"
    )
    assert rate.percent == 50.0  # pooled answers would give 80%, which is wrong here


def test_unknown_is_not_confident_absence_and_roles_can_be_unavailable(final):
    row = next(
        a
        for a in s9.decode_rows((final / "step9/answer_metrics.jsonl").read_bytes(), AnswerMeasure)
        if a.prompt_id == "q3"
    )
    assert stability([row]) == "uncertain"
    assert question_metric([row], "presence").percent == 0.0
    unknown = row.model_copy(update={"recommended": "uncertain"})
    assert question_metric([unknown], "recommended").percent is None


def test_corrupt_report_number_is_detected(final):
    aggregate = Aggregates.model_validate_json((final / "aggregates.json").read_bytes())
    html = (final / "report.html").read_text("utf-8")
    import re

    tampered = re.sub(r'(<span data-metric="[^"]+">)[^<]+', r"\g<1>9999", html, count=1)
    with pytest.raises(ValueError, match="number differs"):
        audit_html(tampered, aggregate)


@pytest.mark.parametrize("name", ["report.html", "aggregates.json", "step10/input/aggregates.json"])
def test_chain_detects_changed_files(final, tmp_path, name):
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(final, copy)
    (copy / name).write_bytes(b"tampered")
    with pytest.raises((ValueError, OSError, GeoAuditError)):
        workflow.verify(copy)


def context():
    text = "Компания производит детали и согласует требования заказчика."
    return SiteContext(
        status="verified_step1",
        source_commit_sha256="1" * 64,
        pages=[
            PageContext(
                page_id="page1",
                url="https://fakel.example.test/services",
                title="Детали",
                text=text,
                source_text_sha256=digest(text.encode()),
                truncated=False,
            )
        ],
        facts=[CompanyFact(fact_id="fact1", text=text, quote=text, page_id="page1")],
        limitations=[],
    )


def test_gap_material_requires_real_page_quote_and_supported_numbers():
    site = context()
    valid = GapSuggestion(
        topic_id="t",
        kind="existing_page",
        page_id="page1",
        title="Детали",
        quote=site.pages[0].text,
    )
    s10.validate_suggestion(valid, {"t"}, site)
    for item in [
        valid.model_copy(update={"page_id": "invented"}),
        valid.model_copy(update={"quote": "Выдуманная цитата"}),
        valid.model_copy(update={"title": "Детали за 99 рублей"}),
    ]:
        with pytest.raises(ValueError):
            s10.validate_suggestion(item, {"t"}, site)


def test_new_outline_requires_company_facts():
    item = GapSuggestion(
        topic_id="t",
        kind="new_page",
        title="Детали для проекта",
        outline=[
            dict(text="Согласование требований", fact_ids=["fact1"]),
            dict(text="Производство деталей", fact_ids=["fact1"]),
            dict(text="Требования заказчика", fact_ids=["fact1"]),
        ],
    )
    s10.validate_suggestion(item, {"t"}, context())
    wrong = item.model_copy(
        update={
            "outline": [part.model_copy(update={"fact_ids": ["fake"]}) for part in item.outline]
        }
    )
    with pytest.raises(ValueError):
        s10.validate_suggestion(wrong, {"t"}, context())


def test_invalid_gap_response_falls_back_without_destroying_plan(final):
    inputs = {name: (final / "step10/input" / name).read_bytes() for name in s10.INPUT_FILES}
    inputs["review-results.json"] = b"broken JSON"
    files = s10.build_artifacts(inputs)
    assert b"quarantined_response" in files["review_intake.jsonl"]
    assert Gaps.model_validate_json(files["gaps.json"]).topics


def test_foreign_gap_job_is_not_accepted(final):
    inputs = {name: (final / "step10/input" / name).read_bytes() for name in s10.INPUT_FILES}
    inputs["review-results.json"] = encoded(
        GapResponse(
            job_id="other", context_sha256="0" * 64, reviewer_kind="manual", suggestions=[]
        ).model_dump(mode="json")
    )
    assert b"quarantined_response" in s10.build_artifacts(inputs)["review_intake.jsonl"]


def test_gap_without_candidate_never_creates_a_model_job(final):
    aggregate = Aggregates.model_validate_json((final / "aggregates.json").read_bytes())
    revised = aggregate.model_copy(
        update={
            "questions": [q.model_copy(update={"name_matches": 1}) for q in aggregate.questions]
        }
    )
    assert s10.select(revised) == []


def test_external_cli_requires_explicit_network_permission():
    with pytest.raises(SystemExit):
        sources.main(["--step9-dir", "unused", "--output-dir", "unused2"])


def test_source_literal_quotes_are_exact_and_do_not_establish_identity(final):
    aggregate = Aggregates.model_validate_json((final / "aggregates.json").read_bytes())
    text = "ФАКЕЛ; FKL; Факелок; другой бренд."
    matches = sources.literal_names(text, aggregate.target)
    assert {m.quote for m in matches} == {"ФАКЕЛ", "FKL"}
    assert all(text[m.start : m.end] == m.quote for m in matches)
    assert next(m for m in matches if m.name == "FKL").ambiguous


def test_failed_external_observation_stays_unknown(final):
    aggregate = Aggregates.model_validate_json((final / "aggregates.json").read_bytes())
    report = SourceInspection(
        source_aggregates_sha256=digest(encoded(aggregate.model_dump(mode="json"))),
        selection_sha256=sources.selection_sha(aggregate),
        synthetic=True,
        observations=[sources.unavailable(t.task_id, t.url) for t in aggregate.source_page_tasks],
        network_attempts=0,
    )
    sources.validate_export(report, aggregate)
    broken = report.model_copy(update={"selection_sha256": "0" * 64})
    with pytest.raises(ValueError):
        sources.validate_export(broken, aggregate)


def test_no_network_in_final_workflow(early, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline final chain attempted network")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    root = tmp_path / "finished"
    assert (
        workflow.run(
            early / "step8", root, policy=MetricPolicy(bootstrap_replicates=100)
        ).model_calls
        == 0
    )
    assert workflow.verify(root).network_requests == 0


@pytest.mark.usefixtures("fake_api")
def test_controlled_complete_zero_to_twelve(monkeypatch, tmp_path):
    from tests.test_step4_to_step8 import (
        test_full_unchanged_zero_to_four_and_adapted_five_to_eight as early_test,
    )

    early_test(monkeypatch, tmp_path)
    publication = next(p for p in (tmp_path / "early/out").iterdir() if (p / "run.json").is_file())
    root = tmp_path / "finished"
    result = workflow.run(
        tmp_path / "complete/step8",
        root,
        site_dir=publication,
        upstream_chain=tmp_path / "complete",
        policy=MetricPolicy(bootstrap_replicates=100),
    )
    assert result.committed and workflow.verify(root).committed
    report = json.loads((root / "report_data.json").read_bytes())
    assert report["site_context_status"] == "verified_step1"
    assert report["aggregates"]["eligible_answers"] == 70


def test_schemas_are_strict_on_impossible_question_counts(final):
    aggregate = Aggregates.model_validate_json((final / "aggregates.json").read_bytes())
    raw = aggregate.questions[0].model_dump(mode="json")
    raw["target_confirmed"] = raw["valid_answers"] + 1
    with pytest.raises(ValidationError):
        type(aggregate.questions[0]).model_validate(raw)
