"""Blind selection, truthful pending state, validated imports and no sample rewrites."""

import hashlib
import json
import socket
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from jsonschema import Draft202012Validator

from geo_audit.step5 import run_step5
from geo_audit.step5.models import Answer, PromptSet, Target
from geo_audit.step5.pipeline import encoded, process
from geo_audit.step6 import run_step6
from geo_audit.step6.classify import annotate
from geo_audit.step7 import run_step7, verify_publication
from geo_audit.step7.__main__ import main
from geo_audit.step7.evaluate import agreements, metric, resolve
from geo_audit.step7.models import SCHEMA_MODELS, ReviewPolicy, ReviewResult
from geo_audit.step7.review import accept_results, select_jobs

ROOT = Path(__file__).resolve().parents[1]


def data_for(texts):
    target = Target(
        target_id="t",
        name="Факел",
        aliases=["Факел", "FKL"],
        ambiguous_aliases=["FKL"],
        domains=["fakel.test"],
    )
    prompts = PromptSet(
        schema_version="1.0",
        prompt_set_id="p",
        country="RU",
        language="ru",
        prompts=[
            dict(prompt_id="q", group="A", text="Какой сервис выбрать?", visibility_eligible=True)
        ],
    )
    rows = [
        dict(
            answer_id=f"a-{i:04}",
            prompt_id="q",
            prompt_set_id="p",
            provider="mock",
            model="fixture",
            origin="synthetic",
            synthetic=True,
            repeat_index=i + 1,
            search_mode="simulated",
            status="ok",
            text=text,
        )
        for i, text in enumerate(texts)
    ]
    mechanical, _, _ = process(prompts, target, b"".join(encoded(row) for row in rows))
    originals = {row["answer_id"]: Answer(**row) for row in rows}
    annotations = [annotate(originals[m.answer_id], m, target) for m in mechanical]
    return annotations, originals, prompts, target


def selected_for(texts, policy=None):
    annotations, originals, prompts, target = data_for(texts)
    policy = policy or ReviewPolicy()
    selected, jobs, eligible = select_jobs(
        annotations, originals, prompts, target, policy, "a" * 64
    )
    return annotations, selected, jobs, eligible


def result_for(job, *, presence="yes", recommended="yes", discouraged="no", kind="model"):
    brands = []
    if presence == "yes":
        alias = "Факел" if "Факел" in job.text else "FKL"
        start = job.text.index(alias)
        identity = dict(quote=alias, start=start, end=start + len(alias))
        quote = dict(quote=job.text, start=0, end=len(job.text))
        brands = [
            dict(
                name="Факел",
                is_target="yes",
                recommended=recommended,
                discouraged=discouraged,
                identity_evidence=[identity],
                recommendation_evidence=[quote] if recommended == "yes" else [],
                discouragement_evidence=[quote] if discouraged == "yes" else [],
            )
        ]
    return dict(
        schema_version="step7-review-1.0",
        job_id=job.job_id,
        text_sha256=job.text_sha256,
        reviewer=dict(
            kind=kind, provider="test-fixture", model="fixture-model", family="fixture-family"
        ),
        status="ok",
        target_presence=presence,
        target_recommended=recommended,
        target_discouraged=discouraged,
        brands=brands,
    )


def test_selection_is_five_percent_union_and_order_independent():
    annotations, originals, prompts, target = data_for(
        ["Рекомендую Факел."] * 40 + ["Рекомендую FKL."] * 5
    )
    policy = ReviewPolicy()
    selected, jobs, eligible = select_jobs(
        annotations, originals, prompts, target, policy, "a" * 64
    )
    again, again_jobs, _ = select_jobs(
        list(reversed(annotations)), originals, prompts, target, policy, "a" * 64
    )
    assert selected == again and jobs == again_jobs
    assert eligible == 45 and sum(s.random_sample for s in selected) == 3
    assert sum(s.uncertain_case for s in selected) == 5
    assert len(selected) == 3 + 5 - sum(s.random_sample and s.uncertain_case for s in selected)
    assert len({job.job_id for job in jobs}) == len(jobs)
    for job in jobs:
        payload = job.model_dump(mode="json")
        assert set(payload) == {
            "schema_version",
            "job_id",
            "text_sha256",
            "text",
            "prompt_text",
            "target",
            "instruction",
            "response_schema",
        }
        assert (
            not {"primary", "answer_id", "random_sample", "uncertain_case", "provider"}
            & payload.keys()
        )
        assert "note" not in payload["target"]


def test_measurement_exclusions_are_not_sampled():
    annotations, originals, prompts, target = data_for(["Рекомендую Факел.", "Рекомендую FKL."])
    annotations[1] = annotations[1].model_copy(update={"measurement_eligible": False})
    selected, _, eligible = select_jobs(
        annotations, originals, prompts, target, ReviewPolicy(), "a" * 64
    )
    assert eligible == 1 and [s.answer_id for s in selected] == [annotations[0].answer_id]


def test_confident_random_sample_is_never_corrected():
    annotations, selection, jobs, _ = selected_for(["Рекомендую Факел."])
    record = ReviewResult.model_validate(result_for(jobs[0], presence="no", recommended="no"))
    effective, changed = resolve(annotations, selection, {record.job_id: record})
    assert changed == 0
    assert effective[0]["target_presence"] == effective[0]["target_recommended"] == "yes"
    assert "target_presence" in effective[0]["confident_disagreements"]
    metrics = agreements(
        annotations, selection, {record.job_id: record}, ReviewPolicy(minimum_comparable=1)
    )
    assert metrics["identity"].agreement_percent == 0
    assert metrics["identity"].reliability == "low"


def test_uncertain_resolves_but_does_not_change_original_agreement_denominator():
    annotations, selection, jobs, _ = selected_for(["Рекомендую FKL."])
    record = ReviewResult.model_validate(result_for(jobs[0]))
    results, intake = accept_results(encoded(record.model_dump(mode="json")), jobs)
    assert intake[0]["outcome"] == "accepted"
    effective, changed = resolve(annotations, selection, results)
    assert changed > 0 and effective[0]["target_presence"] == "yes"
    assert effective[0]["target_recommended"] == "yes"
    assert annotations[0].target_presence == "uncertain"
    metrics = agreements(annotations, selection, results, ReviewPolicy(minimum_comparable=1))
    assert metrics["identity"].comparable == 0 and metrics["identity"].agreement_percent is None


def test_second_uncertain_and_synthetic_never_become_confirmed():
    annotations, selection, jobs, _ = selected_for(["Рекомендую FKL."])
    for record in (
        result_for(jobs[0], presence="uncertain", recommended="uncertain", discouraged="uncertain"),
        result_for(jobs[0], kind="synthetic"),
    ):
        results, _ = accept_results(encoded(record), jobs)
        effective, changed = resolve(annotations, selection, results)
        assert effective[0]["target_presence"] == "uncertain" and changed == 0
    metrics = agreements(annotations, selection, results, ReviewPolicy(minimum_comparable=1))
    assert metrics["identity"].agreement_percent is None


def test_conflicting_identity_does_not_resolve_unrelated_target_roles():
    annotations, selection, jobs, _ = selected_for(["Не могу рекомендовать Факел без проверки."])
    result = ReviewResult.model_validate(result_for(jobs[0], presence="no", recommended="no"))
    effective, changed = resolve(annotations, selection, {result.job_id: result})
    assert (
        effective[0]["target_presence"] == "yes"
        and effective[0]["target_recommended"] == "uncertain"
    )
    assert changed == 0


@pytest.mark.parametrize("matches, expected", [(9, "medium"), (8, "low")])
def test_agreement_ninety_percent_boundary(matches, expected):
    pairs = [("yes", "yes")] * matches + [("yes", "no")] * (10 - matches)
    value = metric(10, pairs, ReviewPolicy(minimum_comparable=10))
    assert value.reliability == expected and value.agreement_percent == matches * 10
    assert (
        metric(20, [("yes", "yes")], ReviewPolicy(minimum_comparable=1)).reliability
        == "insufficient_sample"
    )
    assert metric(0, [], ReviewPolicy()).agreement_percent is None


def test_result_intake_rejects_foreign_jobs_quotes_and_conflicts():
    _, _, jobs, _ = selected_for(["Рекомендую FKL."])
    valid = result_for(jobs[0])
    accepted, intake = accept_results(encoded(valid) + encoded(valid), jobs)
    assert len(accepted) == 1 and [r["outcome"] for r in intake] == ["accepted", "duplicate"]
    other = dict(valid, target_presence="no", target_recommended="no", brands=[])
    accepted, intake = accept_results(encoded(valid) + encoded(other), jobs)
    assert not accepted and all(r["reason"] == "conflicting_review_versions" for r in intake)
    invalid_quote = result_for(jobs[0])
    invalid_quote["brands"][0]["identity_evidence"][0] = dict(quote="XXX", start=0, end=3)
    assert accept_results(encoded(invalid_quote), jobs)[1][0]["reason"] == "invalid_quote"
    assert (
        accept_results(encoded(dict(valid, job_id="foreign")), jobs)[1][0]["reason"]
        == "unassigned_job"
    )
    assert (
        accept_results(encoded(dict(valid, text_sha256="0" * 64)), jobs)[1][0]["reason"]
        == "text_hash_mismatch"
    )


def test_error_review_is_not_a_negative_answer_and_rank_stays_outside_scope():
    annotations, selection, jobs, _ = selected_for(["Рекомендую FKL."])
    value = result_for(
        jobs[0], presence="uncertain", recommended="uncertain", discouraged="uncertain"
    )
    value.update(
        status="error",
        target_presence=None,
        target_recommended=None,
        target_discouraged=None,
        brands=[],
        error_code="rate_limit",
    )
    results, _ = accept_results(encoded(value), jobs)
    effective, changed = resolve(annotations, selection, results)
    assert effective[0]["target_presence"] == "uncertain" and changed == 0
    assert (
        agreements(annotations, selection, results, ReviewPolicy())["identity"].agreement_percent
        is None
    )
    annotations, selection, jobs, _ = selected_for(
        ["Мой рейтинг один:\n1. Факел.\n2. Изумруд.\n\nМой рейтинг два:\n1. Изумруд.\n2. Факел."]
    )
    result = ReviewResult.model_validate(result_for(jobs[0], recommended="no"))
    effective, _ = resolve(annotations, selection, {result.job_id: result})
    assert any("rank_outside_review_scope" in f for f in effective[0]["outstanding_fields"])


@pytest.fixture(scope="module")
def source6(tmp_path_factory):
    root = tmp_path_factory.mktemp("step7-source")
    run_step5(ROOT / "examples/mock_answers_v1/input", root / "step5")
    run_step6(root / "step5", root / "step6")
    return root / "step6"


def test_full_offline_pending_publication(source6, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    before = {p.relative_to(source6): p.read_bytes() for p in source6.rglob("*") if p.is_file()}
    out = tmp_path / "step7"
    result = run_step7(source6, out)
    assert result.eligible_answers == 445 and result.random_sample_count == 23
    assert result.uncertain_count == 103
    assert result.selected_count == 126 - result.overlap_count
    assert result.pending_jobs == result.selected_count and result.accepted_results == 0
    assert result.status == "pending_api_key" and result.independent_review == "not_performed"
    assert all(
        m.agreement_percent is None and m.reliability == "not_assessed"
        for m in result.metrics.values()
    )
    assert (out / "crosscheck.jsonl").read_bytes() == b""
    assert verify_publication(out) == result
    assert before == {
        p.relative_to(source6): p.read_bytes() for p in source6.rglob("*") if p.is_file()
    }
    page = BeautifulSoup((out / "report.html").read_text(encoding="utf-8"), "html.parser")
    assert not page.select("script,iframe,img,link")
    assert "Нет данных" in page.get_text()
    assert main(["--verify", str(out)]) == 0


def test_tampering_is_rejected_even_with_updated_digest(source6, tmp_path):
    out = tmp_path / "out"
    run_step7(source6, out)
    path = out / "resolved_annotations.jsonl"
    data = path.read_bytes().replace(b'"target_presence": "yes"', b'"target_presence": "no"', 1)
    path.write_bytes(data)
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["files"][path.name] = dict(
        sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data)
    )
    (out / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="replay"):
        verify_publication(out)


def test_schemas_are_committed():
    for model in SCHEMA_MODELS:
        schema = model.model_json_schema() | {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:geo-audit:step7:{model.__name__}:"
            + (
                "1.1"
                if model.__name__ in {"Step7Summary", "ResolvedAnnotation", "PublicationManifest"}
                else "1.0"
            ),
        }
        path = ROOT / "schemas/step7" / f"{model.__name__}.schema.json"
        assert json.loads(path.read_text()) == schema
        Draft202012Validator.check_schema(schema)
    for path in (ROOT / "examples/contracts/step7").glob("*.json"):
        value = ReviewResult.model_validate_json(path.read_bytes())
        assert value.reviewer.kind == "synthetic"


def test_low_agreement_produces_a_visible_warning():
    from geo_audit.step7.models import Step7Summary
    from geo_audit.step7.report import render_report

    annotations, selection, jobs, _ = selected_for(["Рекомендую Факел."])
    value = ReviewResult.model_validate(result_for(jobs[0], presence="no", recommended="no"))
    policy = ReviewPolicy(minimum_comparable=1)
    summary = Step7Summary(
        status="complete",
        eligible_answers=1,
        random_sample_count=1,
        uncertain_count=0,
        overlap_count=0,
        selected_count=1,
        accepted_results=1,
        quarantined_rows=0,
        duplicate_rows=0,
        model_reviews=1,
        manual_reviews=0,
        synthetic_reviews=0,
        error_results=0,
        pending_jobs=0,
        resolved_fields=0,
        independent_review="imported_declarations_only",
        model_reviewer=value.reviewer,
        metrics=agreements(annotations, selection, {value.job_id: value}, policy),
    )
    page = BeautifulSoup(render_report(summary, selection, jobs, policy), "html.parser")
    assert "Предупреждение: согласие ниже 90%" in page.get_text()


def test_public_import_of_synthetic_result_is_recorded_but_not_applied(source6, tmp_path):
    prepared = tmp_path / "prepared"
    initial = run_step7(source6, prepared)
    from geo_audit.step7.models import BlindJob

    job = BlindJob.model_validate(
        json.loads((prepared / "blind_jobs.jsonl").read_text(encoding="utf-8").splitlines()[0])
    )
    record = result_for(job, presence="no", recommended="no", kind="synthetic")
    imported = tmp_path / "reviews.jsonl"
    imported.write_bytes(encoded(record))
    out = tmp_path / "imported"
    result = run_step7(source6, out, results_file=imported)
    assert result.synthetic_reviews == 1 and result.model_reviews == result.manual_reviews == 0
    assert result.pending_jobs == initial.pending_jobs and result.resolved_fields == 0
    assert result.independent_review == "not_performed"
    assert all(m.agreement_percent is None for m in result.metrics.values())
    assert verify_publication(out) == result
    assert (out / "resolved_annotations.jsonl").read_bytes() == (
        prepared / "resolved_annotations.jsonl"
    ).read_bytes()


def test_role_agreement_includes_identity_even_for_neutral_roles():
    annotations, selection, jobs, _ = selected_for(["Факел существует."])
    value = ReviewResult.model_validate(result_for(jobs[0], presence="no", recommended="no"))
    metrics = agreements(
        annotations, selection, {value.job_id: value}, ReviewPolicy(minimum_comparable=1)
    )
    assert metrics["identity"].agreement_percent == 0
    assert metrics["target_roles"].agreement_percent == 0


def test_unresolved_brand_identity_cannot_resolve_roles_or_disappear():
    annotations, selection, jobs, _ = selected_for(["Рекомендую FKL."])
    value = result_for(
        jobs[0], presence="uncertain", recommended="uncertain", discouraged="uncertain"
    )
    start = jobs[0].text.index("FKL")
    value["brands"] = [
        dict(
            name="FKL",
            is_target="uncertain",
            recommended="yes",
            discouraged="no",
            identity_evidence=[dict(quote="FKL", start=start, end=start + 3)],
            recommendation_evidence=[dict(quote=jobs[0].text, start=0, end=len(jobs[0].text))],
        )
    ]
    reviews, _ = accept_results(encoded(value), jobs)
    effective, changed = resolve(annotations, selection, reviews)
    assert changed == 0 and not effective[0]["brand_overrides"]
    assert "brands/0/is_target" in effective[0]["outstanding_fields"]


def test_model_cohorts_are_not_blended_in_either_input_order():
    _, _, jobs, _ = selected_for(["Рекомендую FKL.", "Рекомендую FKL для агентства."])
    first, second = result_for(jobs[0]), result_for(jobs[1])
    second["reviewer"]["model"] = "different-model"
    for rows in ((first, second), (second, first)):
        accepted, intake = accept_results(b"".join(encoded(row) for row in rows), jobs)
        assert not accepted
        assert all(
            row["outcome"] == "quarantined" and row["reason"] == "mixed_model_reviewers"
            for row in intake
        )


def test_effective_contract_locks_primary_annotation_and_review_provenance():
    from geo_audit.step7.models import ResolvedAnnotation

    annotations, selection, jobs, _ = selected_for(["Рекомендую FKL."])
    record = ReviewResult.model_validate(result_for(jobs[0]))
    rows, _ = resolve(annotations, selection, {record.job_id: record})
    assert (
        rows[0]["primary_annotation_sha256"]
        == hashlib.sha256(encoded(annotations[0].model_dump(mode="json"))).hexdigest()
    )
    wrong = dict(
        rows[0], field_sources=dict(rows[0]["field_sources"], target_presence="imported_manual")
    )
    with pytest.raises(ValueError, match="reviewer provenance"):
        ResolvedAnnotation.model_validate(wrong)
    wrong = dict(rows[0], outstanding_fields=["target_presence"])
    with pytest.raises(ValueError, match="uncertainty"):
        ResolvedAnnotation.model_validate(wrong)


def test_bad_extra_rows_prevent_complete_status(tmp_path):
    _, originals, prompts, target = data_for(["Рекомендую Факел."])
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "prompts.json").write_text(prompts.model_dump_json(), encoding="utf-8")
    (raw / "target.json").write_text(target.model_dump_json(), encoding="utf-8")
    (raw / "answers.jsonl").write_bytes(
        b"".join(encoded(a.model_dump(mode="json")) for a in originals.values())
    )
    run_step5(raw, tmp_path / "step5")
    run_step6(tmp_path / "step5", tmp_path / "step6")
    source6 = tmp_path / "step6"
    run_step7(source6, tmp_path / "prepared")
    from geo_audit.step7.models import BlindJob

    job = BlindJob.model_validate_json((tmp_path / "prepared/blind_jobs.jsonl").read_bytes())
    results = tmp_path / "bad-results.jsonl"
    results.write_bytes(encoded(result_for(job)) + b'{"job_id":"unassigned"}\n')
    summary = run_step7(source6, tmp_path / "partial", results_file=results)
    assert summary.status == "partial" and summary.quarantined_rows == 1
    assert summary.accepted_results == 1 and summary.pending_jobs == 0


def test_manual_resolution_is_not_model_agreement():
    annotations, selection, jobs, _ = selected_for(["Рекомендую FKL."])
    record = ReviewResult.model_validate(result_for(jobs[0], kind="manual"))
    rows, changed = resolve(annotations, selection, {record.job_id: record})
    assert changed and rows[0]["field_sources"]["target_presence"] == "imported_manual"
    assert (
        agreements(annotations, selection, {record.job_id: record}, ReviewPolicy())[
            "identity"
        ].agreement_percent
        is None
    )


def test_competitor_conflict_is_preserved_without_sample_correction():
    annotations, selection, jobs, _ = selected_for(
        ["Рекомендованные сервисы:\n- Факел.\n- Изумруд."]
    )
    value = result_for(jobs[0])
    start = jobs[0].text.index("Изумруд")
    value["brands"].append(
        dict(
            name="Изумруд",
            is_target="no",
            recommended="no",
            discouraged="no",
            identity_evidence=[dict(quote="Изумруд", start=start, end=start + len("Изумруд"))],
        )
    )
    records, _ = accept_results(encoded(value), jobs)
    rows, changed = resolve(annotations, selection, records)
    assert changed == 0 and not rows[0]["brand_overrides"]
    assert rows[0]["brand_disagreements"][0]["fields"] == ["recommended"]
    assert (
        agreements(annotations, selection, records, ReviewPolicy(minimum_comparable=1))[
            "competitor_roles"
        ].agreement_percent
        == 0
    )


def test_case_and_whitespace_do_not_split_the_same_model_cohort():
    _, _, jobs, _ = selected_for(["Рекомендую FKL.", "Рекомендую FKL для агентства."])
    first, second = result_for(jobs[0]), result_for(jobs[1])
    second["reviewer"].update(provider=" TEST-FIXTURE ", family="FIXTURE-FAMILY")
    accepted, intake = accept_results(encoded(first) + encoded(second), jobs)
    assert len(accepted) == 2 and all(row["outcome"] == "accepted" for row in intake)


def test_many_brands_in_one_answer_cannot_imply_medium_reliability():
    value = metric(20, [("yes", "yes")] * 20, ReviewPolicy(), comparable_answers=1)
    assert value.agreement_percent == 100 and value.comparable_answers == 1
    assert value.reliability == "insufficient_sample"
