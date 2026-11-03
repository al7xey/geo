"""Check the synthetic dataset on disk, without calling any models or URLs."""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

BASE = Path(__file__).resolve().parents[1] / "examples" / "mock_answers_v1"
FIELDS = ("target_presence", "target_recommended", "target_discouraged", "target_cited")


def read_json(name: str):
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def read_lines(name: str):
    return [json.loads(line) for line in (BASE / name).read_text(encoding="utf-8").splitlines()]


def main() -> None:
    manifest = read_json("manifest.json")
    prompts = read_json("input/prompts.json")
    mapping = {p["prompt_id"]: p for p in prompts["prompts"]}
    answers = read_lines("input/answers.jsonl")
    gold = read_lines("oracle/expected_decisions.jsonl")
    assert len({g["case_id"] for g in gold}) == 32
    assert len(mapping) == 20 and len(answers) == len(gold) == 480
    assert Counter(p["group"] for p in mapping.values()) == dict.fromkeys("ABCD", 5)
    by_id = {a["answer_id"]: a for a in answers}
    labels = {g["answer_id"]: g for g in gold}
    assert len(by_id) == 480 and by_id.keys() == labels.keys()
    slots = {(a["prompt_id"], a["provider"], a["repeat_index"]) for a in answers}
    assert len(slots) == 480
    assert Counter(a["provider"] for a in answers) == dict.fromkeys(manifest["providers"], 160)
    target = read_json("input/target.json")["domains"][0]
    for a in answers:
        assert a["synthetic"] and a["origin"] == "synthetic"
        assert a["group"] == mapping[a["prompt_id"]]["group"]
        assert a["prompt_set_id"] == prompts["prompt_set_id"]
        assert a["text_sha256"] == hashlib.sha256(a["text"].encode()).hexdigest()
        assert "case_id" not in a and not any(field in a for field in FIELDS)
        for source in a["sources"]:
            assert urlsplit(source["url"]).hostname.endswith(".test")
        g = labels[a["answer_id"]]
        assert g["eligible"] == (a["status"] == "ok")
        for span in g["evidence"]:
            assert span["quote"] and a["text"][span["start"] : span["end"]] == span["quote"]
        for field in FIELDS:
            assert g[field] in ("yes", "no", "uncertain") if g["eligible"] else g[field] is None
        if g["target_cited"] == "yes":
            assert any(urlsplit(s["url"]).hostname == target for s in a["sources"])
        if g["eligible"] and g["target_presence"] == "yes":
            assert any(s["field"] == "target_presence" for s in g["evidence"])
    all_routed = []
    for group in "ABCD":
        rows = read_lines(f"groups/{group}.jsonl")
        assert len(rows) == 120
        for row in rows:
            assert row["group"] == group == by_id[row["answer_id"]]["group"]
        all_routed.extend(r["answer_id"] for r in rows)
    assert len(all_routed) == len(set(all_routed)) == 480
    assert set(all_routed) == set(by_id)
    for row in read_json("oracle/expected_aggregates.json"):
        selected = [
            a
            for a in answers
            if all(
                row[key] == "all" or row[key] == a[key]
                for key in ("provider", "group", "prompt_id")
            )
        ]
        usable = [a for a in selected if labels[a["answer_id"]]["eligible"]]
        assert row["input_count"] == len(selected)
        assert row["excluded_count"] == len(selected) - len(usable)
        for field in FIELDS:
            c = Counter(labels[a["answer_id"]][field] for a in usable)
            actual = row["answer_micro"][field]
            assert (actual["yes_count"], actual["no_count"], actual["uncertain_count"]) == (
                c["yes"],
                c["no"],
                c["uncertain"],
            )
            assert actual["eligible_count"] == len(usable)
            for numerator, name in (
                (c["yes"], "confirmed_percent"),
                (c["yes"] + c["uncertain"], "possible_upper_percent"),
                (c["yes"] + c["no"], "decision_coverage_percent"),
            ):
                expected = 100 * numerator / len(usable) if usable else None
                assert actual[name] == expected
            slot_keys = {(a["provider"], a["prompt_id"]) for a in usable}
            for name in ("confirmed_percent", "possible_upper_percent"):
                rates = []
                for provider, prompt_id in slot_keys:
                    values = [
                        labels[a["answer_id"]][field]
                        for a in usable
                        if a["provider"] == provider and a["prompt_id"] == prompt_id
                    ]
                    hits = sum(
                        v == "yes" or (name == "possible_upper_percent" and v == "uncertain")
                        for v in values
                    )
                    rates.append(100 * hits / len(values))
                assert math.isclose(
                    row["provider_prompt_macro"][field][name], sum(rates) / len(rates)
                )
    selection = read_json("oracle/expected_review_selection.json")
    eligible = [g["answer_id"] for g in gold if g["eligible"]]
    expected_sample = random.Random(selection["seed"]).sample(
        eligible, math.ceil(len(eligible) * 0.05)
    )
    assert selection["random_sample_ids"] == sorted(expected_sample)
    uncertain = {g["answer_id"] for g in gold if g["needs_semantic_review"]}
    assert set(selection["uncertain_ids"]) == uncertain
    assert set(selection["union_ids"]) == uncertain | set(expected_sample)
    for name, digest in manifest["file_sha256"].items():
        assert (BASE / name).resolve().is_relative_to(BASE.resolve())
        assert hashlib.sha256((BASE / name).read_bytes()).hexdigest() == digest
    print("PASS: 480 answers; 20 prompts; 32 cases; routing, evidence, metrics, sample and hashes.")


if __name__ == "__main__":
    main()
