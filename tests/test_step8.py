"""Offline identity scopes, source classification, bounded jobs and immutable handoffs."""

import json
import shutil
import socket
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from jsonschema import Draft202012Validator

from geo_audit.errors import GeoAuditError
from geo_audit.step5 import run_step5
from geo_audit.step5.models import Answer, PromptSet, Target
from geo_audit.step5.pipeline import encoded, process
from geo_audit.step6 import run_step6
from geo_audit.step6.classify import annotate
from geo_audit.step7 import run_step7
from geo_audit.step7.evaluate import resolve
from geo_audit.step7.models import ResolvedAnnotation
from geo_audit.step8 import run_step8, verify_publication
from geo_audit.step8.__main__ import main
from geo_audit.step8.grouping import group_brands, group_domains, pending_jobs
from geo_audit.step8.models import (
    SCHEMA_MODELS,
    BrandMap,
    DomainTypes,
    EntityCatalog,
    Step8Policy,
)
from geo_audit.step8.names import normalize_name
from geo_audit.step8.pipeline import INPUT_FILES, build_artifacts
from geo_audit.step8.report import render_report

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
            answer_id=f"a-{i}",
            prompt_id="q",
            prompt_set_id="p",
            provider="fixture",
            model="mock",
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
    annotations = [
        annotate(Answer.model_validate(rows[m.source_line - 1]), m, target) for m in mechanical
    ]
    effective, _ = resolve(annotations, [], {})
    return (
        annotations,
        [ResolvedAnnotation.model_validate(row) for row in effective],
        target,
        mechanical,
    )


def grouped(texts, catalog=None, policy=None):
    annotations, effective, target, _ = data_for(texts)
    return group_brands(
        annotations, effective, target, catalog or EntityCatalog(), policy or Step8Policy()
    )


@pytest.mark.parametrize(
    "left,right",
    [
        ("  «Blue  Link» ", "blue link"),
        ("Ｂｌｕｅ", "blue"),
        ("БРЕНД", "бренд"),
    ],
)
def test_normalization_equivalences(left, right):
    assert normalize_name(left) == normalize_name(right)


@pytest.mark.parametrize(
    "left,right",
    [
        ("Blue-Link", "Blue Link"),
        ("Факел", "Fakel"),
        ("Link.io", "Linkio"),
    ],
)
def test_normalization_preserves_meaningful_differences(left, right):
    assert normalize_name(left) != normalize_name(right)


def test_target_context_and_unresolved_alias_never_merge():
    occurrences, entities = grouped(
        ["Рекомендую Факел.", "Рекомендую FKL.", "FKL означает Факел. Рекомендую FKL."]
    )
    unresolved = [e for e in entities if e.identity_scope == "unresolved"]
    assert len(unresolved) == 1 and unresolved[0].canonical_name == "FKL"
    assert len({e.entity_id for e in entities}) == 2
    assert all(o.entity_id != unresolved[0].entity_id for o in occurrences if o.is_target == "yes")


def test_same_spelling_merge_retains_each_answer_and_quote():
    occurrences, entities = grouped(["1. BlueLink — сервис.", "1. BLUELINK — сервис."])
    assert len(entities) == 1 and len(occurrences) == 2
    assert len(entities[0].answer_ids) == 2
    assert {v for o in occurrences for v in o.observed_variants} == {"BlueLink", "BLUELINK"}
    assert set(entities[0].occurrence_ids) == {o.occurrence_id for o in occurrences}


def test_transliteration_and_similar_spelling_only_create_jobs():
    occurrences, entities = grouped(
        ["1. Fakel — сервис.\n2. Факель — сервис.\n3. BlueLinks — сервис.\n4. BlueLink — сервис."]
    )
    jobs, evaluations, complete, _ = pending_jobs(entities, occurrences, [], Step8Policy())
    assert len(entities) == 4
    assert evaluations >= 1 and complete
    assert any(j.reason == "similar_spelling" for j in jobs)
    occurrences, entities = grouped(["1. Орион — сервис.\n2. Orion — сервис."])
    jobs, _, _, _ = pending_jobs(entities, occurrences, [], Step8Policy())
    assert len(entities) == 2 and any(j.reason == "transliteration" for j in jobs)
    assert all(not j.executed and j.status == "pending_api_key" for j in jobs)


def test_catalog_only_merges_observed_non_target_names():
    catalog = EntityCatalog.model_validate(
        {
            "alias_groups": [
                {"aliases": ["Орион", "Orion", "Unseen"], "reason": "Fixture identity record."}
            ]
        }
    )
    _, entities = grouped(["1. Орион — сервис.\n2. Orion — сервис."], catalog)
    assert len(entities) == 1 and entities[0].merge_basis == "manual_catalog"
    assert "Unseen" not in entities[0].variants
    assert entities[0].canonical_name in {"Орион", "Orion"}
    catalog = EntityCatalog.model_validate(
        {
            "alias_groups": [
                {"aliases": ["Факел", "FKL"], "reason": "Does not resolve contextual ambiguity."}
            ]
        }
    )
    _, entities = grouped(["Рекомендую Факел.", "Рекомендую FKL."], catalog)
    assert len(entities) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"alias_groups": [{"aliases": ["Blue", "BLUE"], "reason": "duplicate"}]},
        {
            "alias_groups": [
                {"aliases": ["A", "B"], "reason": "x"},
                {"aliases": ["b", "C"], "reason": "x"},
            ]
        },
        {"domain_labels": [{"host": "a.test", "kind": "made_up", "reason": "x"}]},
        {"domain_labels": [{"host": "a.test", "kind": "media", "reason": " "}]},
        {
            "domain_labels": [
                {"host": "A.test", "kind": "media", "reason": "x"},
                {"host": "a.test", "kind": "forum", "reason": "x"},
            ]
        },
    ],
)
def test_catalog_rejects_conflicts_and_unknown_types(payload):
    with pytest.raises(ValueError):
        EntityCatalog.model_validate(payload)


def test_imported_identity_uses_reviewed_name_and_exact_primary_hash():
    annotations, effective, target, _ = data_for(["Рекомендую FKL."])
    assert annotations[0].brands[0].is_target == "uncertain"
    payload = effective[0].model_dump(mode="json")
    payload.update(
        review_state="imported_manual",
        review_job_id="review-1",
        brand_overrides=[
            dict(
                primary_brand_index=0,
                reviewed_name="FKL",
                changes={"is_target": "no"},
                review_job_id="review-1",
            )
        ],
    )
    row = ResolvedAnnotation.model_validate(payload)
    occurrences, entities = group_brands(annotations, [row], target, EntityCatalog(), Step8Policy())
    assert (
        entities[0].canonical_name == "FKL" and entities[0].identity_scope == "non_target_candidate"
    )
    assert occurrences[0].role_sources["is_target"] == "imported_manual"
    assert occurrences[0].review_job_id == "review-1"
    payload["primary_annotation_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        group_brands(
            annotations,
            [ResolvedAnnotation.model_validate(payload)],
            target,
            EntityCatalog(),
            Step8Policy(),
        )


def test_source_hosts_use_exact_catalog_no_suffix_traps_or_fetch():
    _, _, target, mechanical = data_for(
        [
            "Источники: https://fakel.test/a https://sub.fakel.test/b "
            "https://fakel.test.evil.test/c https://news.test/a https://sub.news.test/b"
        ]
    )
    catalog = EntityCatalog.model_validate(
        {"domain_labels": [{"host": "news.test", "kind": "media", "reason": "Fixture content."}]}
    )
    domains = {d.host: d for d in group_domains(mechanical, target, catalog)}
    assert domains["fakel.test"].owner == domains["sub.fakel.test"].owner == "first_party"
    assert domains["fakel.test.evil.test"].owner == "external"
    assert domains["news.test"].kind == "media"
    assert domains["sub.news.test"].kind == "unknown"
    assert all(d.resolution_status == "not_attempted" for d in domains.values())


def test_pair_and_domain_budgets_preserve_entities():
    occurrences, entities = grouped(
        ["1. BlueLink — сервис.\n2. BlueLinks — сервис.\n3. BlueLinked — сервис."]
    )
    _, _, target, mechanical = data_for(["https://one.test/a https://two.test/b"])
    domains = group_domains(mechanical, target, EntityCatalog())
    jobs, evaluated, complete, deferred = pending_jobs(
        entities,
        occurrences,
        domains,
        Step8Policy(max_pair_evaluations=1, max_pair_jobs=0, max_domain_jobs=1),
    )
    assert evaluated == 1 and not complete and deferred == 1
    assert len(jobs) == 1 and len(entities) == 3
    with pytest.raises(ValueError, match="budget"):
        grouped(
            ["1. BlueLink — сервис.\n2. Orion — сервис."], policy=Step8Policy(max_distinct_groups=1)
        )


@pytest.fixture(scope="module")
def publication(tmp_path_factory):
    root = tmp_path_factory.mktemp("step8")
    inputs = ROOT / "examples" / "mock_answers_v1" / "input"
    run_step5(inputs, root / "step5")
    run_step6(root / "step5", root / "step6")
    run_step7(root / "step6", root / "step7")
    run_step8(root / "step7", root / "step8")
    return root


def test_full_mock_handoff_and_replay_without_network(publication, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Network forbidden in step 8")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    summary = verify_publication(publication / "step8")
    assert summary.source_answers == 480 and summary.model_calls == summary.network_requests == 0
    assert summary.status == "partial" and summary.unknown_source_types == 2
    root = publication / "step8"
    brands = BrandMap.model_validate_json((root / "brand_map.json").read_bytes())
    domains = DomainTypes.model_validate_json((root / "domain_types.json").read_bytes())
    assert len(brands.entities) == summary.entity_groups
    assert len(domains.domains) == summary.source_hosts
    assert (root / "input" / "answers.jsonl").read_bytes() == (
        publication / "step7" / "input" / "answers.jsonl"
    ).read_bytes()


@pytest.mark.parametrize(
    "filename", ["resolved_annotations.jsonl", "crosscheck.jsonl", "step7-manifest.json"]
)
def test_captured_source_tampering_rejected(publication, filename):
    root = publication / "step8" / "input"
    snapshots = {name: (root / name).read_bytes() for name in INPUT_FILES}
    if filename == "step7-manifest.json":
        manifest = json.loads(snapshots[filename])
        manifest["source_fingerprint"] = "0" * 64
        snapshots[filename] = encoded(manifest)
    else:
        snapshots[filename] += b" "
    with pytest.raises(ValueError):
        build_artifacts(snapshots)


def test_new_only_and_cli(publication, capsys):
    assert main(["--verify", str(publication / "step8")]) == 0
    assert '"model_calls": 0' in capsys.readouterr().out
    with pytest.raises((OSError, ValueError, GeoAuditError)):
        run_step8(publication / "step7", publication / "step8")
    with pytest.raises(ValueError, match="separate"):
        run_step8(publication / "step7", publication / "step7" / "nested")
    assert (
        main(
            [
                "--step7-dir",
                str(publication / "missing"),
                "--output-dir",
                str(publication / "no-output"),
            ]
        )
        == 2
    )
    assert not (publication / "no-output").exists()


def test_derived_tampering_detected_even_with_updated_file_hash(publication, tmp_path):
    from geo_audit.step5.pipeline import digest

    output = tmp_path / "forged"
    shutil.copytree(publication / "step8", output)
    data = json.loads((output / "brand_map.json").read_bytes())
    data["entities"][0]["canonical_name"] = "Forged Brand"
    forged = encoded(data)
    (output / "brand_map.json").write_bytes(forged)
    manifest = json.loads((output / "manifest.json").read_bytes())
    manifest["files"]["brand_map.json"] = dict(sha256=digest(forged), size_bytes=len(forged))
    (output / "manifest.json").write_bytes(encoded(manifest))
    with pytest.raises(ValueError, match="replay"):
        verify_publication(output)


def test_report_has_no_executable_untrusted_markup(publication):
    root = publication / "step8"
    summary = verify_publication(root)
    entities = BrandMap.model_validate_json((root / "brand_map.json").read_bytes()).entities
    entities[0] = entities[0].model_copy(
        update={"canonical_name": '<img src=x onerror="alert(1)">'}
    )
    html = render_report(summary, entities, [], []).decode()
    soup = BeautifulSoup(html, "html.parser")
    assert not soup.find_all(["script", "img", "iframe", "link"])
    assert "&lt;img" in html and "default-src 'none'" in html
    assert "Проценты видимости рассчитываются на шаге 9" in soup.get_text()


def test_schemas_are_current_and_cover_actual_outputs(publication):
    for model in SCHEMA_MODELS:
        schema = json.loads(
            (ROOT / "schemas" / "step8" / f"{model.__name__}.schema.json").read_text("utf-8")
        )
        Draft202012Validator.check_schema(schema)
        assert {
            k: v for k, v in schema.items() if k not in {"$id", "$schema"}
        } == model.model_json_schema()
    for filename, model in [("brand_map.json", BrandMap), ("domain_types.json", DomainTypes)]:
        data = json.loads((publication / "step8" / filename).read_bytes())
        Draft202012Validator(model.model_json_schema()).validate(data)
