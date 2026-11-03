"""Actual unchanged 0-4 functions feed adapted 5-8; only controlled fixture transports."""

import asyncio
import hashlib
import json
import random
import socket
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from geo_audit.step5.collector import build_artifacts, publish, verify_publication
from geo_audit.step5.models import Answer, PromptSet
from geo_audit.step5.pipeline import encoded
from geo_audit.step5.replay import CURRENT, replay_session, reuse_replay
from geo_audit.step5.workflow import main, run, verify
from geo_audit.step6.models import Annotation
from geo_audit.step8.models import Step8Summary

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import step2_fast as F  # noqa: E402
import step3_review as R  # noqa: E402
import step4_collect as S  # noqa: E402
import step4_handoff as H  # noqa: E402
import steps0_8 as wrapper  # noqa: E402

from tests.test_step4_collect import fake_api  # noqa: E402,F401 - registers controlled API fixture


def snapshots(rows=None):
    prompts = [
        dict(
            id=f"q{i}",
            text="Рекомендации компаний" if i != 3 else "Сравни Факел с другими",
            level=i,
            origin="custom" if i == 4 else "suggested",
            has_brand=i == 3,
            counts_in_organic_visibility=i != 3,
        )
        for i in range(1, 5)
    ]
    doc = dict(
        schema_version=H.SCHEMA,
        domain="fakel.example.test",
        language="русский",
        country="Россия",
        name_variants=["Факел", "FKL"],
        prompts=prompts,
        prompts_sha256=H.prompts_sha256(prompts),
    )
    profile = dict(
        domain=doc["domain"],
        name_variants=[dict(variant="Факел", ambiguous=False), dict(variant="FKL", ambiguous=True)],
    )
    if rows is None:
        rows = [row(i, "chatgpt", 1) for i in range(1, 5)]
    return {
        "step4-input.json": encoded(doc),
        "profile.json": encoded(profile),
        "answers.jsonl": b"".join(encoded(r) for r in rows),
        "policy.json": encoded({"synthetic": True}),
    }


def row(q, system="chatgpt", repeat=1, status="ok", text="Рекомендую Факел."):
    return dict(
        answer_id=f"source-{q}-{system}-{repeat}-{status}",
        prompt_id=f"q{q}",
        system=system,
        model="fixture-model",
        repeat=repeat,
        status=status,
        text=text if status == "ok" else "",
        sources=["https://fakel.example.test/about"] if status == "ok" else [],
        raw_file="raw/unopened.json.gz",
        raw_sha256="0" * 64,
    )


def test_adapter_levels_flags_ambiguity_and_source_quotes():
    artifacts = build_artifacts(snapshots())
    prompts = PromptSet.model_validate_json(artifacts["prompts.json"])
    assert [p.group for p in prompts.prompts] == list("ABCD")
    assert [p.visibility_eligible for p in prompts.prompts] == [True, True, False, True]
    target = json.loads(artifacts["target.json"])
    assert target["ambiguous_aliases"] == ["FKL"]
    answers = [Answer.model_validate_json(line) for line in artifacts["answers.jsonl"].splitlines()]
    assert len(answers) == 4 and all(a.origin == "synthetic" for a in answers)
    assert (
        answers[0].text == "Рекомендую Факел." and answers[0].sources[0].origin == "step4_collector"
    )
    assert json.loads(artifacts["summary.json"])["raw_files_opened"] == 0


def test_profile_www_domain_matches_collectors_own_site_rule():
    data = snapshots()
    profile = json.loads(data["profile.json"])
    profile["domain"] = "www.fakel.example.test"
    data["profile.json"] = encoded(profile)
    assert json.loads(build_artifacts(data)["target.json"])["domains"] == ["fakel.example.test"]


def test_latest_retry_semantics_and_twenty_percent_system_exclusion():
    rows = [row(1, repeat=1, status="error"), row(1, repeat=1)]
    rows += [row(2, "gemini", i, status="error" if i <= 2 else "ok") for i in range(1, 6)]
    rows += [row(2, "claude", i, status="error" if i == 1 else "ok") for i in range(1, 6)]
    rows += [row(4, "gemini", 6, status="skipped")]
    artifacts = build_artifacts(snapshots(rows))
    summary = json.loads(artifacts["summary.json"])
    assert summary["replaced_attempts"] == 1 and summary["excluded_systems"] == ["gemini"]
    answers = [Answer.model_validate_json(line) for line in artifacts["answers.jsonl"].splitlines()]
    assert all(not a.collection_eligible for a in answers if a.provider == "gemini")
    assert all(
        a.collection_eligible for a in answers if a.provider == "claude"
    )  # exactly 20% is allowed
    assert next(a for a in answers if a.provider == "chatgpt").status == "ok"
    skipped = next(a for a in answers if a.provider == "gemini" and a.repeat_index == 6)
    assert skipped.status == "error" and skipped.error == {"code": "budget_skipped"}


@pytest.mark.parametrize(
    "kind", ["checksum", "brand", "profile", "prompt", "duplicate_keys", "bad_json", "bool_level"]
)
def test_adapter_rejects_broken_approval_or_rows_before_publication(tmp_path, kind):
    data = snapshots()
    doc = json.loads(data["step4-input.json"])
    if kind == "checksum":
        doc["prompts"][0]["text"] = "Changed after review"
    elif kind == "brand":
        doc["prompts"][2]["counts_in_organic_visibility"] = True
        doc["prompts_sha256"] = H.prompts_sha256(doc["prompts"])
    elif kind == "bool_level":
        doc["prompts"][0]["level"] = True
        doc["prompts_sha256"] = H.prompts_sha256(doc["prompts"])
    elif kind == "profile":
        p = json.loads(data["profile.json"])
        p["domain"] = "other.example.test"
        data["profile.json"] = encoded(p)
    elif kind == "prompt":
        data["answers.jsonl"] = encoded({**row(1), "prompt_id": "unknown"})
    elif kind == "duplicate_keys":
        data["answers.jsonl"] = b'{"answer_id":"a","answer_id":"b"}'
    else:
        data["answers.jsonl"] = b"not json"
    data["step4-input.json"] = encoded(doc)
    with pytest.raises(ValueError):
        publish(data, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()


def test_adapter_replay_and_source_read_only(tmp_path):
    data = snapshots()
    root = tmp_path / "bundle"
    publish(data, root)
    assert verify_publication(root)["selected_answers"] == 4
    for name, value in data.items():
        assert (root / "source" / name).read_bytes() == value
    (root / "target.json").write_bytes(b"{}")
    with pytest.raises(ValueError):
        verify_publication(root)


def test_replay_cache_is_scoped_bounded_and_not_mutable_by_consumer():
    calls = []
    from geo_audit.step5.collector import AdapterPolicy

    @reuse_replay
    def builder(snapshots):
        calls.append(snapshots)
        return {"out": snapshots["value"]}, AdapterPolicy()

    with replay_session():
        first, _ = builder({"value": b"one"})
        first["out"] = b"corrupted by caller"
        assert builder({"value": b"one"})[0]["out"] == b"one" and len(calls) == 1
        builder({"value": b"two"})
        assert len(calls) == 2 and CURRENT.get() is not None
    assert CURRENT.get() is None
    builder({"value": b"one"})
    assert len(calls) == 3


@pytest.mark.usefixtures("fake_api")
def test_full_unchanged_zero_to_four_and_adapted_five_to_eight(monkeypatch, tmp_path):
    import step2_checks as C

    from tests.fakes import VirtualTime
    from tests.test_step1_pipeline import BUSINESS, execute_pipeline, html

    original_init = VirtualTime.__init__

    def dated_clock(self):
        original_init(self)
        # Virtual sleeps advance instantly. Keep the resulting publication in the
        # real past even on fast runners, while remaining within the 7-day TTL.
        self.epoch = datetime.now(UTC) - timedelta(hours=1)

    monkeypatch.setattr(VirtualTime, "__init__", dated_clock)
    runtime, result, _, _, _ = execute_pipeline(
        monkeypatch, tmp_path / "early", entry=html("Ромашка. " + BUSINESS)
    )
    data = C.load_input(runtime.store.io.root)
    corpus, stats = F.build_corpus(data, runtime.store.io.root, 100000)
    assert stats["pages_sent"] and "Ромашка" in corpus
    domain = data["domain"]
    url = data["pages"][0]["url"]
    quote = "We manufacture precision metal components for industrial customers."
    profile = dict(
        data_status="ok",
        domain=domain,
        name_variants=[
            dict(
                variant="Ромашка",
                ambiguous=False,
                type="official",
                source=dict(quote="Ромашка", url=url),
            )
        ],
        proprietary_terms=[],
        category=dict(main="производство деталей", basis=[dict(quote=quote, url=url)]),
        activity=[dict(fact="Производит детали", quote=quote, url=url)],
    )
    rng = random.Random(17)

    class ModelFixture:
        def log(self, message):
            pass

        async def ask(self, tag, model, effort, system, user, max_tokens=0):
            if tag.startswith("profile"):
                return (
                    "<profile_json>" + json.dumps(profile, ensure_ascii=False) + "</profile_json>"
                )
            level = int(tag[1])
            out = []
            for pos in F.PLAN:
                if not pos.startswith(f"{level}."):
                    continue
                branded = pos in F.BRANDED
                n = {1: 10, 2: 20, 3: 40 if branded else 25, 4: 60}[level]
                words = ["Ромашка"] if branded else []
                words += [f"условие{rng.randrange(10000000)}" for _ in range(n - len(words))]
                out.append(dict(plan_position=pos, text=" ".join(words)))
            return (
                "<prompts_json>"
                + json.dumps({"prompts": out}, ensure_ascii=False)
                + "</prompts_json>"
            )

    step2 = tmp_path / "step2"
    step2.mkdir()
    approved_profile, removed, _ = asyncio.run(
        F.stage_profile(
            ModelFixture(),
            step2,
            [("fixture", "none")],
            "profile",
            corpus,
            {url: data["pages"][0]["text"]},
            "",
        )
    )
    good, issues, global_errors = asyncio.run(
        F.stage_queries(
            ModelFixture(),
            "fixture",
            "none",
            {i: "" for i in range(1, 5)},
            approved_profile,
            "",
            domain,
            {},
        )
    )
    assert len(good) == 40 and not issues and not global_errors
    prompts = [{**good[pos], "id": f"q{i:02d}"} for i, pos in enumerate(sorted(good), 1)]
    (step2 / "profile.json").write_bytes(encoded(approved_profile))
    (step2 / "prompts.json").write_bytes(encoded({"prompts": prompts}))
    (step2 / "step2_report.json").write_bytes(
        encoded(dict(domain=domain, language="русский", country="Россия"))
    )
    state = R.initial_state(approved_profile, prompts)
    step3 = tmp_path / "step3"
    saved = R.save(step2, step3, state)
    assert saved["saved"], saved
    doc = H.load_step4_input(step3 / "step4_input.json")
    collected = tmp_path / "step4"
    collected.mkdir()
    col = S.Collector(doc, collected, ["chatgpt", "gemini", "claude"], max_cost=100, repeats=1)
    rows = asyncio.run(col.run(doc["prompts"]))
    assert len(rows) == 120
    source_hash = hashlib.sha256((collected / "answers.jsonl").read_bytes()).hexdigest()

    def forbidden(*args, **kwargs):
        pytest.fail("downstream tried to use a network")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    output = tmp_path / "complete"
    run(step3 / "step4_input.json", collected, output, synthetic=True)
    assert verify(output).network_requests == 0
    adapter = json.loads((output / "bundle/summary.json").read_bytes())
    assert adapter["questions"] == 40 and adapter["branded_questions"] == 5
    assert adapter["organic_questions"] == 35 and adapter["excluded_systems"] == ["gemini"]
    annotations = [
        Annotation.model_validate_json(line)
        for line in (output / "step6/annotations.jsonl").read_bytes().splitlines()
    ]
    assert sum(a.measurement_eligible for a in annotations) == 70
    assert all(not a.measurement_eligible for a in annotations if a.provider == "gemini")
    assert (
        Step8Summary.model_validate_json(
            (output / "step8/summary.json").read_bytes()
        ).source_answers
        == 120
    )
    assert hashlib.sha256((collected / "answers.jsonl").read_bytes()).hexdigest() == source_hash
    assert main(["--verify", str(output)]) == 0


def test_wrapper_preserves_collector_arguments_and_dry_run(monkeypatch, tmp_path):
    calls = []

    def process(argv, check):
        calls.append(argv)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(wrapper.subprocess, "run", process)
    monkeypatch.setattr(wrapper, "analyze", lambda argv: pytest.fail("dry-run must not analyze"))
    assert (
        wrapper.main(
            [
                "collect",
                "--step4-input",
                "input.json",
                "--answers-dir",
                "collected",
                "--output-dir",
                "result",
                "--dry-run",
                "--systems",
                "chatgpt",
            ]
        )
        == 0
    )
    assert "--dry-run" in calls[0] and calls[0][-2:] == ["--systems", "chatgpt"]


def test_workflow_invalid_inputs_do_not_publish(tmp_path):
    assert (
        main(
            [
                "--step4-input",
                str(tmp_path / "absent.json"),
                "--answers-dir",
                str(tmp_path / "a"),
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "out").exists()


def test_upstream_zero_to_four_sources_are_byte_identical_to_supplied_archive():
    root = Path(__file__).resolve().parents[1]
    hashes = json.loads((root / "docs/UPSTREAM_0_4_SHA256.json").read_text("utf-8"))
    for name, sha in hashes.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == sha, name


def test_collect_rejects_missing_profile_before_any_paid_process(monkeypatch, tmp_path):
    monkeypatch.setattr(
        wrapper.subprocess, "run", lambda *a, **k: pytest.fail("must fail before collector")
    )
    assert (
        wrapper.main(
            [
                "collect",
                "--step4-input",
                str(tmp_path / "missing.json"),
                "--answers-dir",
                str(tmp_path / "answers"),
                "--output-dir",
                str(tmp_path / "new"),
                "--max-cost",
                "1",
            ]
        )
        == 2
    )


def test_cache_budget_falls_back_to_real_replay(monkeypatch):
    import geo_audit.step5.replay as replay
    from geo_audit.step5.collector import AdapterPolicy

    monkeypatch.setattr(replay, "LIMIT", 1)
    calls = []

    @reuse_replay
    def builder(snapshots):
        calls.append(1)
        return {"out": b"more-than-budget"}, AdapterPolicy()

    with replay_session():
        builder({"in": b"value"})
        builder({"in": b"value"})
        assert len(calls) == 2 and CURRENT.get().size == 0
