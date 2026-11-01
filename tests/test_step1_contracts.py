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


@pytest.mark.parametrize(
    "mutation",
    [
        "unselected_fetched",
        "source_selected",
        "source_discovery",
        "short_useful",
        "business_without_main_text",
        "duplicate_material",
        "quote_wrong_evidence",
        "unknown_derivation",
        "nontext_quote",
        "incomplete_positive",
        "foreign_import",
        "wrong_derivation_source",
        "identity_collision",
        "dangling_alias_chain",
    ],
)
def test_graph_invariants_before_any_files_are_opened(mutation):
    raw = sample_result().model_dump(mode="json")
    page = raw["crawl"]["pages"][0]
    if mutation == "unselected_fetched":
        raw["crawl"]["candidates"][0]["selected"] = False
        raw["crawl"]["coverage"]["selected_candidates"] = 0
    elif mutation == "source_selected":
        raw["source"]["selected_evidence_id"] = "missing"
    elif mutation == "source_discovery":
        raw["crawl"]["candidates"][0]["sources"][0]["source_id"] = "missing"
    elif mutation == "short_useful":
        page["main_text"]["text_chars"] = 100
    elif mutation == "business_without_main_text":
        page["main_text"] = None
        page["useful_candidate"] = False
        raw["crawl"]["coverage"]["extracted_pages"] = 0
        raw["crawl"]["coverage"]["useful_pages"] = 0
        raw["crawl"]["material"].update(
            state="insufficient",
            can_start_step2=False,
            useful_page_ids=[],
        )
        raw["run"]["status"] = "needs_input"
    elif mutation == "duplicate_material":
        raw["crawl"]["material"]["useful_page_ids"].append("page-1")
    elif mutation == "quote_wrong_evidence":
        page["business_quotes"][0]["evidence_id"] = "html-1"
        page["business_quotes"][0]["text_hash"] = raw["evidence"][0]["file"]["sha256"]
        page["main_text"]["text_hash"] = raw["evidence"][0]["file"]["sha256"]
        raw["evidence"][1]["file"]["sha256"] = raw["evidence"][0]["file"]["sha256"]
    elif mutation == "unknown_derivation":
        raw["evidence"][1]["source_evidence_id"] = None
    elif mutation == "nontext_quote":
        page["signals"] = [
            dict(
                kind="address",
                value="Example",
                strength="explicit",
                evidence_id="html-1",
                quote=dict(
                    evidence_id="html-1",
                    text_hash=raw["evidence"][0]["file"]["sha256"],
                    start=0,
                    end=7,
                    text="Example",
                ),
            )
        ]
    elif mutation == "incomplete_positive":
        raw["evidence"][0]["file"]["complete"] = False
    elif mutation == "foreign_import":
        raw["evidence"][0].update(source_run_id="foreign", original_evidence_id="html-1")
    elif mutation == "wrong_derivation_source":
        unrelated = deepcopy(raw["evidence"][0])
        unrelated["evidence_id"] = "html-2"
        unrelated["file"]["path"] = "raw/html-2.bin"
        raw["evidence"].append(unrelated)
        raw["evidence"][1]["source_evidence_id"] = "html-2"
    elif mutation == "identity_collision":
        page["page_id"] = "html-1"
        raw["crawl"]["material"]["useful_page_ids"] = ["html-1"]
    else:
        candidate = raw["crawl"]["candidates"][0]
        candidate.update(state="duplicate", alias_of="candidate-2")
        second = deepcopy(candidate)
        second.update(
            candidate_id="candidate-2",
            url="https://example.com/other/",
            alias_of="missing",
            ordinal=1,
        )
        raw["crawl"]["candidates"].append(second)
        raw["crawl"]["pages"] = []
        raw["crawl"]["coverage"].update(
            discovered_urls=2,
            selected_candidates=2,
            fetch_successes=0,
            unique_html_pages=0,
            extracted_pages=0,
            useful_pages=0,
        )
        raw["crawl"]["material"].update(
            state="needs_description", can_start_step2=False, useful_page_ids=[]
        )
        raw["run"]["status"] = "needs_input"
    with pytest.raises(ValidationError):
        Step1Result.model_validate_json(json.dumps(raw))
