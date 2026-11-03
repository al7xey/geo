"""Offline step 5 boundaries: no semantic claims, no network and no lost records."""

import hashlib
import json
import os
import socket
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from geo_audit.errors import GeoAuditError
from geo_audit.psl import load_bundled_psl
from geo_audit.step5.__main__ import main
from geo_audit.step5.extract import Extractor
from geo_audit.step5.models import SCHEMA_MODELS, Answer, PromptSet, Target
from geo_audit.step5.pipeline import process, run_step5, verify_publication

ROOT = Path(__file__).resolve().parents[1]


def target():
    return Target(
        target_id="target",
        name="GoGetLinks",
        aliases=["GoGetLinks", "GGL", "Café"],
        ambiguous_aliases=["GGL"],
        domains=["gogetlinks.example.test"],
    )


def prompts():
    return PromptSet(
        schema_version="1.0",
        prompt_set_id="set-1",
        country="RU",
        language="ru",
        prompts=[
            dict(prompt_id=f"q-{g}", group=g, text="Test question", visibility_eligible=True)
            for g in "ABCD"
        ],
    )


def answer(**updates):
    value = dict(
        answer_id="a-1",
        prompt_id="q-A",
        prompt_set_id="set-1",
        provider="fixture",
        model="test",
        origin="synthetic",
        synthetic=True,
        repeat_index=1,
        search_mode="simulated",
        status="ok",
        text="GoGetLinks",
    )
    value.update(updates)
    return value


def lines(*values):
    return b"".join((json.dumps(v, ensure_ascii=False) + "\n").encode() for v in values)


def bundle(tmp_path, *values):
    path = tmp_path / "input"
    path.mkdir()
    (path / "prompts.json").write_text(prompts().model_dump_json(), encoding="utf-8")
    (path / "target.json").write_text(target().model_dump_json(), encoding="utf-8")
    (path / "answers.jsonl").write_bytes(lines(*values))
    return path


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_480_answers_no_network_and_no_oracle(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    source = ROOT / "examples/mock_answers_v1/input"
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    out = tmp_path / "result"
    result = run_step5(source, out)
    assert verify_publication(out) == result
    assert result.accepted == 480 and result.quarantined == result.duplicates == 0
    assert result.prompt_groups == dict.fromkeys("ABCD", 120)
    assert result.delivery_statuses == dict(ok=445, empty=15, error=12, truncated=8)
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    assert all((out / "input" / name).read_bytes() == data for name, data in before.items())
    ids = [r["answer_id"] for g in "ABCD" for r in read_lines(out / "prompt_groups" / f"{g}.jsonl")]
    assert len(ids) == len(set(ids)) == 480
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["committed"]
    for name, expected in manifest["files"].items():
        data = (out / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected["sha256"]
        assert len(data) == expected["size_bytes"]
    for category in ("calculated_estimate", "hypothesis", "recommendation"):
        assert (out / "categories" / f"{category}.jsonl").read_bytes() == b""
    facts = read_lines(out / "categories/observed_fact.jsonl")
    assert len(facts) == result.evidence_categories["observed_fact"]
    assert sum(f["kind"] == "answer_record" for f in facts) == 480
    originals = {a["answer_id"]: a for a in read_lines(source / "answers.jsonl")}
    for f in facts:
        assert f["scope"] == "collected_answer_only"
        if f["kind"] == "name_match":
            assert originals[f["answer_id"]]["text"][f["start"] : f["end"]] == f["quote"]


def test_bad_rows_are_accounted_and_good_rows_survive():
    data = lines(
        answer(),
        answer(answer_id="b", prompt_id="absent"),
        answer(answer_id="c", repeat_index=2, group="D"),
        answer(answer_id="d", repeat_index=3, text_sha256="0" * 64),
    )
    data += b'{"answer_id":"private-secret",bad}\n\n'
    records, intake, summary = process(prompts(), target(), data)
    assert [r.answer_id for r in records] == ["a-1"]
    assert summary.input_lines == 6 and summary.quarantined == 5
    assert len(intake) == 6
    assert {r.reason for r in intake} >= {"unknown_prompt", "group_conflict", "text_hash_mismatch"}


def test_duplicates_same_text_repeats_and_conflicts():
    a = answer()
    b = answer(answer_id="a-2", repeat_index=2)
    records, intake, summary = process(
        prompts(), target(), lines(a, dict(reversed(list(a.items()))), b)
    )
    assert len(records) == 2 and summary.duplicates == 1
    assert intake[1].outcome == "duplicate"
    for values in ((a, answer(text="changed")), (answer(text="changed"), a)):
        records, intake, summary = process(prompts(), target(), lines(*values))
        assert not records and summary.quarantined == 2
        assert all(r.reason == "answer_id_conflict" for r in intake)


def test_different_ids_in_same_logical_slot_are_both_quarantined():
    records, intake, summary = process(prompts(), target(), lines(answer(), answer(answer_id="b")))
    assert not records and summary.quarantined == 2
    assert {r.reason for r in intake} == {"logical_slot_conflict"}


def test_group_is_from_prompt_not_number_or_answer_text():
    records, _, _ = process(prompts(), target(), lines(answer(prompt_id="q-D", text="No list.")))
    assert records[0].group == "D" and records[0].annotation_status == "unreviewed"


def test_unicode_original_positions_boundaries_and_ambiguity():
    text = "😀 «ＧｏＧｅｔＬｉｎｋｓ» GGL GGLPlus GoGetLinksPlus Cafe\u0301"
    candidates, _, _ = Extractor(target(), load_bundled_psl()).extract(Answer(**answer(text=text)))
    assert [c.quote for c in candidates] == ["ＧｏＧｅｔＬｉｎｋｓ", "GGL", "Cafe\u0301"]
    assert [c.ambiguous for c in candidates] == [False, True, False]
    assert all(text[c.start : c.end] == c.quote for c in candidates)


def test_sources_and_body_url_are_separate_and_no_redirect_fetch():
    a = Answer(
        **answer(
            text="https://GoGetLinks.example.test/path).",
            sources=[
                dict(url="https://gogetlinks.example.test/path", origin="api_annotation"),
                dict(url="https://gogetlinks.example.test.evil.test", origin="api_annotation"),
                dict(url="https://other.test/?q=gogetlinks.example.test", origin="api_annotation"),
                dict(
                    url="https://redirect.test/url?to=https%3A%2F%2Fgogetlinks.example.test",
                    origin="api_annotation",
                ),
                dict(url="https://user:secret@gogetlinks.example.test", origin="api_annotation"),
                dict(url="javascript:alert(1)", origin="manual"),
            ],
        )
    )
    candidates, sources, invalid = Extractor(target(), load_bundled_psl()).extract(a)
    assert invalid == 2 and len(sources) == 4
    assert sum(s.target_host_match for s in sources) == 1
    assert sources[0].origins == ["citation", "text"]
    assert candidates and all(c.location == "text_url" for c in candidates)
    assert all(s.resolution_status == "not_attempted" for s in sources)


def test_citation_does_not_become_name_presence():
    records, _, _ = process(
        prompts(),
        target(),
        lines(
            answer(
                text="Без названий.",
                sources=[dict(url="https://gogetlinks.example.test", origin="api_annotation")],
            )
        ),
    )
    assert records[0].target_domain_in_citations and not records[0].target_name_candidate


def test_inactive_ineligible_and_delivery_are_not_semantic_no():
    p = prompts()
    p = p.model_copy(
        update={
            "prompts": [
                p.prompts[0].model_copy(update={"visibility_eligible": False}),
                p.prompts[1].model_copy(update={"active": False}),
            ]
        }
    )
    records, intake, summary = process(
        p,
        target(),
        lines(
            answer(status="error", text=""),
            answer(answer_id="b", prompt_id="q-B"),
        ),
    )
    assert summary.accepted == summary.quarantined == 1
    assert records[0].status == "error" and not records[0].visibility_eligible
    assert intake[1].reason == "inactive_prompt"


def test_invalid_metadata_is_fatal_before_publication(tmp_path):
    source = bundle(tmp_path, answer())
    value = prompts().model_dump(mode="json")
    value["prompts"].append(value["prompts"][0])
    (source / "prompts.json").write_text(json.dumps(value))
    with pytest.raises(ValueError):
        run_step5(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_output_is_new_separate_and_commit_is_last(tmp_path, monkeypatch):
    source = bundle(tmp_path, answer())
    for out in (source, source / "child", tmp_path):
        with pytest.raises(ValueError):
            run_step5(source, out)
    out = tmp_path / "out"
    run_step5(source, out)
    with pytest.raises(GeoAuditError):
        run_step5(source, out)
    import geo_audit.step5.pipeline as pipeline

    original = pipeline.EvidenceStore.atomic_bytes

    def fail(self, name, data, **kwargs):
        if name == "mechanical.jsonl":
            raise OSError("simulated write failure")
        return original(self, name, data, **kwargs)

    monkeypatch.setattr(pipeline.EvidenceStore, "atomic_bytes", fail)
    broken = tmp_path / "interrupted"
    with pytest.raises(OSError):
        run_step5(source, broken)
    assert not (broken / "manifest.json").exists()


def test_budgets_duplicate_json_keys_and_cli_sanitization(tmp_path, monkeypatch, capsys):
    import geo_audit.step5.pipeline as pipeline

    source = bundle(tmp_path, answer())
    (source / "answers.jsonl").write_bytes(b'{"text":"secret", "text":"other"}\n')
    assert main(["--input-dir", str(source), "--output-dir", str(tmp_path / "out")]) == 1
    assert "secret" not in capsys.readouterr().out
    monkeypatch.setattr(pipeline, "MAX_ROWS", 0)
    assert main(["--input-dir", str(source), "--output-dir", str(tmp_path / "out2")]) == 2
    assert not (tmp_path / "out2").exists()
    assert "secret" not in capsys.readouterr().out


def test_hardlinked_input_is_rejected(tmp_path):
    source = bundle(tmp_path, answer())
    try:
        os.link(source / "answers.jsonl", tmp_path / "linked.jsonl")
    except OSError:
        pytest.skip("hardlinks unavailable")
    with pytest.raises(GeoAuditError):
        run_step5(source, tmp_path / "out")


def test_committed_schema_files_match_models():
    for model in SCHEMA_MODELS:
        schema = json.loads((ROOT / "schemas/step5" / f"{model.__name__}.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        assert schema == model.model_json_schema() | {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:geo-audit:step5:{model.__name__}:1.0",
        }


def test_shared_extraction_budget_fails_before_publication(tmp_path, monkeypatch):
    import geo_audit.step5.pipeline as pipeline

    source = bundle(tmp_path, answer())
    monkeypatch.setattr(pipeline, "MAX_TOTAL_OBSERVATIONS", 0)
    with pytest.raises(ValueError, match="shared extraction budget"):
        run_step5(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_same_input_new_publications_are_byte_identical(tmp_path):
    source = bundle(tmp_path, answer())
    run_step5(source, tmp_path / "one")
    run_step5(source, tmp_path / "two")
    first = {
        p.relative_to(tmp_path / "one"): p.read_bytes()
        for p in (tmp_path / "one").rglob("*")
        if p.is_file()
    }
    second = {
        p.relative_to(tmp_path / "two"): p.read_bytes()
        for p in (tmp_path / "two").rglob("*")
        if p.is_file()
    }
    assert first == second


def test_invalid_row_does_not_poison_valid_logical_slot():
    valid = answer()
    bad = answer(answer_id="bad", text_sha256="0" * 64)
    for values in ((valid, bad), (bad, valid)):
        records, intake, summary = process(prompts(), target(), lines(*values))
        assert [r.answer_id for r in records] == ["a-1"]
        assert summary.quarantined == 1
        assert any(r.reason == "text_hash_mismatch" for r in intake)


def test_empty_input_is_partial_not_success():
    records, intake, summary = process(prompts(), target(), b"")
    assert not records and not intake
    assert summary.status == "partial" and "no_accepted_answers" in summary.warnings


def test_citation_provenance_and_unknown_suffix():
    a = Answer(
        **answer(
            sources=[
                dict(url="https://gogetlinks.example.test", origin="manual"),
                dict(url="https://GoGetLinks.example.test/", origin="api_annotation"),
            ]
        )
    )
    _, sources, _ = Extractor(target(), load_bundled_psl()).extract(a)
    assert len(sources) == 1 and sources[0].citation_indices == [0, 1]
    assert not sources[0].psl_known and sources[0].registrable_domain is None


def test_html_is_escaped_self_contained_and_has_all_records(tmp_path):
    import base64

    from bs4 import BeautifulSoup

    payload = '</pre><script>attack()</script><img src="https://evil.test/x"> GoGetLinks'
    source = bundle(tmp_path, answer(text=payload))
    out = tmp_path / "out"
    run_step5(source, out)
    html = (out / "report.html").read_text(encoding="utf-8")
    page = BeautifulSoup(html, "html.parser")
    assert len(page.select("article.answer")) == 1
    assert len(page.find_all("script")) == 1
    assert not page.find_all(["img", "iframe", "link", "form"])
    assert page.select_one("pre").get_text() == payload
    assert page.select_one("mark").get_text() == "GoGetLinks"
    assert all(page.find(id=id_) for id_ in ("search", "provider", "group", "status", "signal"))
    policy = page.find("meta", attrs={"http-equiv": "Content-Security-Policy"})["content"]
    for tag in ("script", "style"):
        content = page.find(tag).string
        checksum = base64.b64encode(hashlib.sha256(content.encode()).digest()).decode()
        assert f"'sha256-{checksum}'" in policy
    assert "default-src 'none'" in policy


def test_verifier_rejects_tampering_even_with_updated_hash(tmp_path):
    source = bundle(tmp_path, answer())
    out = tmp_path / "out"
    run_step5(source, out)
    path = out / "mechanical.jsonl"
    record = json.loads(path.read_text())
    record["target_name_candidate"] = False
    altered = lines(record)
    path.write_bytes(altered)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["mechanical.jsonl"] = {
        "sha256": hashlib.sha256(altered).hexdigest(),
        "size_bytes": len(altered),
    }
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="source replay"):
        verify_publication(out)


def test_verifier_rejects_missing_marker_and_unknown_manifest_paths(tmp_path):
    source = bundle(tmp_path, answer())
    out = tmp_path / "out"
    run_step5(source, out)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["../outside"] = {"sha256": "0" * 64, "size_bytes": 0}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="manifest mismatch"):
        verify_publication(out)
    manifest_path.unlink()
    with pytest.raises(GeoAuditError):
        verify_publication(out)


def test_verify_cli_succeeds_without_output_directory(tmp_path, capsys):
    source = bundle(tmp_path, answer())
    out = tmp_path / "out"
    run_step5(source, out)
    assert main(["--verify", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["accepted"] == 1
