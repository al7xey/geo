"""Future crawl contracts reject inconsistent material without touching the network."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from geo_audit.contracts import CURRENT_ALGORITHM_VERSION, CURRENT_SCHEMA_VERSION
from geo_audit.models import Header, Step1Input
from geo_audit.step1.config import Step1Config
from geo_audit.step1.models import QuoteRef, Step1Header, Step1Result, Step2Input
from geo_audit.step1.schemas import MODELS, schema_for
from tools.generate_step1_artifacts import artifacts, invalid_examples, sample_result

ROOT = Path(__file__).resolve().parents[1]


def test_roundtrip_schema_and_distinct_versions():
    result = sample_result()
    raw = result.model_dump_json()
    assert Step1Result.model_validate_json(raw) == result
    validator = Draft202012Validator(schema_for("Step1Result"), format_checker=FormatChecker())
    validator.validate(json.loads(raw))
    for name in MODELS:
        Draft202012Validator.check_schema(schema_for(name))
    assert CURRENT_SCHEMA_VERSION == "1.2.0"
    assert CURRENT_ALGORITHM_VERSION == "step0-1.2"
    legacy = (ROOT / "examples/contracts/step1-input.json").read_bytes()
    assert Step1Input.model_validate_json(legacy).crawl_permission == "unknown"
    assert (
        "step1-1.0.0" not in Step1Input.model_json_schema()["properties"]["schema_version"]["enum"]
    )


@pytest.mark.parametrize("case", invalid_examples(), ids=lambda c: c["case"])
def test_invalid_examples_are_rejected(case):
    with pytest.raises(ValidationError):
        Step1Result.model_validate_json(json.dumps(case["payload"]))


@pytest.mark.parametrize("mutation", ["duplicate", "cycle", "foreign", "text_hash", "config"])
def test_evidence_and_identity_invariants(mutation):
    raw = sample_result().model_dump(mode="json")
    if mutation == "duplicate":
        raw["evidence"].append(deepcopy(raw["evidence"][0]))
    elif mutation == "cycle":
        raw["evidence"][0]["source_evidence_id"] = "text-1"
        raw["evidence"][0]["transform_version"] = "bad-cycle"
    elif mutation == "foreign":
        raw["evidence"][0]["run_id"] = "foreign"
    elif mutation == "text_hash":
        raw["crawl"]["pages"][0]["main_text"]["text_hash"] = "0" * 64
    else:
        raw["config"]["max_selected"] = 2
    with pytest.raises(ValidationError):
        Step1Result.model_validate_json(json.dumps(raw))


def test_nullable_extra_finite_utc_and_terminal_gates():
    base = sample_result().model_dump(mode="json")
    for key, value in (
        ("title", ...),
        ("unexpected", True),
        ("content_state", "whatever"),
    ):
        raw = deepcopy(base)
        if value is ...:
            del raw["crawl"]["pages"][0][key]
        else:
            raw["crawl"]["pages"][0][key] = value
        with pytest.raises(ValidationError):
            Step1Result.model_validate_json(json.dumps(raw))
    for value in ("2026-10-31T12:00:00", "2026-10-31T12:00:00+03:00"):
        raw = deepcopy(base)
        raw["run"]["finished_at"] = value
        with pytest.raises(ValidationError):
            Step1Result.model_validate_json(json.dumps(raw))
    cfg = base["config"]
    assert not Step1Config.model_validate_json(json.dumps(cfg)).browser_enabled
    assert not Step1Config.model_validate_json(json.dumps(cfg)).ua_probes_enabled
    with pytest.raises(ValidationError):
        Step1Config(source_step0_config_sha256="0" * 64, browser_page_seconds=float("nan"))


def test_multivalue_headers_do_not_widen_step0_allowlist():
    values = tuple(
        Step1Header(name="x-robots-tag", value=v) for v in ("noindex", "googlebot: none")
    )
    assert [v.value for v in values] == ["noindex", "googlebot: none"]
    with pytest.raises(ValidationError):
        Header(name="x-robots-tag", value="noindex", redacted=False)


def test_quote_offsets_and_user_provenance_have_no_fake_page():
    with pytest.raises(ValidationError):
        QuoteRef(evidence_id="ev", text_hash="0" * 64, start=1, end=9, text="abc")
    schema = schema_for("Step1Result")["$defs"]["UserMaterial"]
    assert "page_id" not in schema["properties"]
    assert schema["properties"]["source_type"]["const"] == "user_provided"


def test_step2_corpus_gate_and_synthetic_not_live_handoff():
    raw = artifacts()["examples/contracts/step1/step2-input.json"]
    value = Step2Input.model_validate_json(raw)
    assert value.result.run.budget.total_deadline_at.year == 2026
    assert value.result.run.stop_reason == "SYNTHETIC_ONLY"
    assert Step2Input.model_validate_json(value.model_dump_json()) == value
    data = json.loads(raw)
    data["useful_texts"] = []
    with pytest.raises(ValidationError):
        Step2Input.model_validate_json(json.dumps(data))


def test_generated_step1_artifacts_match():
    for path, body in artifacts().items():
        assert (ROOT / path).read_bytes() == body, path
