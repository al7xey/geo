import asyncio
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from geo_audit import (
    Budget,
    GeoAuditError,
    SiteValidationResult,
    Step0Config,
    Step1Input,
    validate_site,
)
from geo_audit.cli import main
from geo_audit.evidence import EvidenceStore
from geo_audit.handoff import build_step1_input, verify_step1_input
from geo_audit.models import EvidenceFile, ResolvedTarget
from geo_audit.policy import http_fallback_eligible
from geo_audit.schemas import MODELS, schema_for
from geo_audit.urls import normalize_input

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples/contracts"


def load(name="site-ok"):
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")), ids=lambda p: p.stem)
def test_examples_validate_with_python_and_json_schema(path):
    mapping = {
        "config": "Step0Config",
        "fetch-attempt": "FetchAttempt",
        "evidence": "EvidenceRecord",
        "budget": "BudgetSnapshot",
        "step1-input": "Step1Input",
        "redirect-hop": "RedirectHop",
        "error": "ErrorDetail",
    }
    name = mapping.get(path.stem, "SiteValidationResult")
    raw = path.read_bytes()
    value = MODELS[name].model_validate_json(raw)
    assert MODELS[name].model_validate_json(value.model_dump_json()) == value
    schema = schema_for(name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(json.loads(raw))


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "ok"),
        ("can_enter_step1", True),
        ("effective_url", "https://example.com/"),
    ],
)
def test_failed_result_cannot_claim_success(field, value):
    data = load("failed-http-403")
    data[field] = value
    with pytest.raises(ValidationError):
        SiteValidationResult.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_nullable",
        "dangling_evidence",
        "wrong_origin",
        "wrong_counter",
        "reset_budget",
        "incomplete_body",
        "unknown_host",
        "schema_version",
        "unknown_field",
        "unverified_tls",
        "error_as_http",
        "expanded_scope",
        "wrong_parent",
        "empty_entity",
        "output_failure",
    ],
)
def test_positive_contract_rejects_inconsistent_artifacts(mutation):
    data = load()
    if mutation == "missing_nullable":
        del data["parent_run_id"]
    elif mutation == "dangling_evidence":
        data["evidence"] = []
    elif mutation == "wrong_origin":
        data["origin"] = "http://example.com"
    elif mutation == "wrong_counter":
        data["budget"]["step0_attempts"] = 0
    elif mutation == "reset_budget":
        data["budget"]["total_attempts"] = 0
    elif mutation == "incomplete_body":
        data["attempts"][0]["body_complete"] = False
    elif mutation == "unknown_host":
        data["attempts"][0]["resolved_target"]["host"] = "other.example"
    elif mutation == "schema_version":
        data["schema_version"] = "0.0"
    elif mutation == "unknown_field":
        data["can_crawl"] = True
    elif mutation == "unverified_tls":
        data["attempts"][0]["tls_state"] = "not_observed"
    elif mutation == "error_as_http":
        data["attempts"][0]["transport_error"] = load("error") | {"category": "http"}
    elif mutation == "expanded_scope":
        data["allowed_hosts"].append("shop.example.com")
    elif mutation == "wrong_parent":
        data["attempts"][0]["parent_attempt_id"] = "nonexistent"
    elif mutation == "empty_entity":
        data["evidence"][0]["files"][0]["size_bytes"] = 0
    elif mutation == "output_failure":
        data["errors"] = [
            {
                "code": "OUTPUT_WRITE_FAILED",
                "category": "output",
                "message": "write failed",
                "retryable": False,
                "attempt_id": None,
            }
        ]
        data["reason_codes"] = ["OUTPUT_WRITE_FAILED"]
    with pytest.raises(ValidationError):
        SiteValidationResult.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "192.0.2.1",
    ],
)
def test_target_contract_rejects_nonpublic_ips(ip):
    with pytest.raises(ValidationError):
        ResolvedTarget(
            host="example.com", port=443, addresses=(ip,), connected_ip=None, enforcement="pinned"
        )


def test_target_contract_rejects_rebinding():
    with pytest.raises(ValidationError):
        ResolvedTarget(
            host="example.com",
            port=443,
            addresses=("8.8.8.8",),
            connected_ip="1.1.1.1",
            enforcement="pinned",
        )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("example.com", "https://example.com/"),
        ("//example.com", "https://example.com/"),
        (" http://EXAMPLE.com:80/ru/?a=1&a=2#x ", "http://example.com/ru/?a=1&a=2"),
        (
            "https://пример.рф/Services?b=2&a=%2f",
            "https://xn--e1afmkfd.xn--p1ai/Services?b=2&a=%2f",
        ),
        ("https://example.com:443/", "https://example.com/"),
        ("https://example.com/?#fragment", "https://example.com/?"),
        ("https://[2606:4700:4700::1111]/", "https://[2606:4700:4700::1111]/"),
    ],
)
def test_normalization_preserves_intent(raw, expected):
    assert normalize_input(raw).url == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://user:pass@example.com",
        "ftp://example.com",
        "https://example.com:99999",
        "https://example.com:0",
        "https://example.com\n",
        "https://a\\b/",
        "https://example.com/%ZZ",
        "https://example.com/a/../b",
        "https://2130706433/",
        "http://0x7f000001/",
        "http://0x7f.0.0.1/",
        "https://example.com:",
    ],
)
def test_invalid_urls_never_normalize(raw):
    with pytest.raises(GeoAuditError):
        normalize_input(raw)


@pytest.mark.parametrize(
    "code,expected",
    [
        ("HTTPS_TLS_CERTIFICATE_ERROR", True),
        ("NETWORK_TIMEOUT", True),
        ("DNS_NXDOMAIN", False),
        ("DNS_TEMPORARY", False),
        ("TARGET_NOT_PUBLIC", False),
        ("REDIRECT_EXTERNAL_HOST", False),
    ],
)
def test_fallback_error_policy(code, expected):
    assert (
        http_fallback_eligible(
            initial_url="https://example.com/",
            error_code=code,
            status_code=None,
            enabled=True,
            fallback_already_used=False,
            retries_exhausted=True,
            budget_available=True,
        )
        is expected
    )


def test_http_response_never_triggers_fallback():
    assert not http_fallback_eligible(
        initial_url="https://example.com/",
        error_code="NETWORK_TIMEOUT",
        status_code=403,
        enabled=True,
        fallback_already_used=False,
        retries_exhausted=True,
        budget_available=True,
    )


def test_budget_counts_real_transport_starts_and_shared_remaining_time():
    seconds = [0.0]
    config = Step0Config(step0_max_attempts=1)
    budget = Budget(config, clock=lambda: seconds[0])
    budget.reserve_attempt()
    with pytest.raises(GeoAuditError, match="Request budget"):
        budget.reserve_attempt()
    seconds[0] = 60.0
    assert budget.remaining_seconds() == 0
    assert budget.remaining_seconds(step=1) == 240
    budget.reserve_attempt(step=1)
    snapshot = budget.snapshot()
    assert (snapshot.step0_attempts, snapshot.total_attempts) == (1, 2)
    seconds[0] = 300.0
    with pytest.raises(GeoAuditError, match="Time budget"):
        budget.reserve_attempt(step=1)


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:/escape", "raw\\escape", "raw/../x"])
def test_evidence_contract_rejects_path_escape(path):
    with pytest.raises(ValidationError):
        EvidenceFile(
            path=path, sha256="a" * 64, size_bytes=1, representation="entity_bytes", complete=True
        )


def test_real_hash_verification_and_no_refetch():
    site = SiteValidationResult.model_validate_json(json.dumps(load()))
    store = EvidenceStore(EXAMPLES / "run")
    value = build_step1_input(site, store, now=site.finished_at)
    assert value.selected_response == site.attempts[0]
    assert value.crawl_permission == "unknown"
    data = value.model_dump(mode="json")
    data["budget"]["step0_attempts"] = 0
    with pytest.raises(ValidationError):
        Step1Input.model_validate_json(json.dumps(data))
    with pytest.raises(GeoAuditError):
        verify_step1_input(value, store, now=site.finished_at + timedelta(seconds=300))


def test_store_writes_and_detects_corruption(tmp_path):
    store = EvidenceStore(tmp_path)
    store.prepare()
    blob = store.write_blob(b"abc", representation="entity_bytes", complete=True)
    record = SiteValidationResult.model_validate_json(json.dumps(load())).evidence[0]
    record = record.model_copy(update={"files": (blob,)})
    store.verify(record)
    (tmp_path / blob.path).write_bytes(b"xyz")
    with pytest.raises(GeoAuditError, match="corrupt"):
        store.verify(record)


def test_publish_refuses_dangling_files(tmp_path):
    site = SiteValidationResult.model_validate_json(json.dumps(load()))
    store = EvidenceStore(tmp_path)
    store.prepare()
    with pytest.raises(GeoAuditError):
        store.publish(site)
    assert not (tmp_path / "site.json").exists()


def test_publish_and_atomic_write_failure(tmp_path, monkeypatch):
    import geo_audit.evidence as module

    site = SiteValidationResult.model_validate_json(json.dumps(load()))
    store = EvidenceStore(tmp_path)
    store.prepare()
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw/entry.html").write_bytes((EXAMPLES / "run/raw/entry.html").read_bytes())
    store.publish(site)
    assert (tmp_path / "manifest.json").exists()
    assert SiteValidationResult.model_validate_json((tmp_path / "site.json").read_bytes()) == site

    def denied(*_):
        raise PermissionError("synthetic disk failure")

    monkeypatch.setattr(module.os, "replace", denied)
    with pytest.raises(GeoAuditError) as error:
        store.publish(site)
    assert error.value.detail.code == "OUTPUT_WRITE_FAILED"
    assert not list(tmp_path.glob("*.tmp"))


def test_unimplemented_api_and_cli_fail_explicitly_before_network(capsys):
    with pytest.raises(GeoAuditError) as error:
        asyncio.run(validate_site("example.com", Step0Config()))
    assert error.value.detail.code == "IMPLEMENTATION_NOT_AVAILABLE"
    assert main(["step0", "https://example.com/?secret=test"]) == 4
    captured = capsys.readouterr()
    assert captured.out == "" and "secret" not in captured.err
    assert json.loads(captured.err)["category"] == "implementation"


def test_cli_help_and_safe_validation_errors(tmp_path, capsys):
    completed = subprocess.run(
        [sys.executable, "-m", "geo_audit", "--help"], capture_output=True, text=True, check=True
    )
    assert "live network runner not yet implemented" in completed.stdout
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"input_url":"https://example.com/?secret=test"}', encoding="utf-8")
    assert main(["validate", "SiteValidationResult", str(invalid)]) == 2
    assert "secret" not in capsys.readouterr().err


def test_generated_files_are_in_sync():
    subprocess.run(
        [sys.executable, "tools/generate_step0_artifacts.py", "--check"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )


def test_archived_v1_contract_remains_readable():
    result = SiteValidationResult.model_validate_json(
        (EXAMPLES / "legacy/site-ok-v1.json").read_bytes()
    )
    assert result.schema_version == "1.0.0" and result.algorithm_version == "step0-1.0"
