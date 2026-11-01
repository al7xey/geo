import asyncio
import gzip
import json
import ssl

import pytest

from geo_audit import Budget, GeoAuditError, Step0Config, validate_site
from geo_audit.evidence import EvidenceStore
from geo_audit.handoff import load_site_result, load_step1_input, prepare_step1
from geo_audit.http_client import SafeHttpClient
from geo_audit.models import SiteValidationResult
from tests.fakes import Connector, Resolver, VirtualTime, install_connector, wire


def execute(
    monkeypatch,
    tmp_path,
    scripts,
    *,
    url="example.com/ru/?secret=local",
    config=None,
    resolver=None,
):
    cfg = config or Step0Config()
    clock = VirtualTime()
    connector = Connector(scripts)
    install_connector(monkeypatch, connector)
    resolver = resolver or Resolver()
    client = SafeHttpClient(cfg, resolver=resolver, timing=clock.services())
    budget = Budget(cfg, timing=clock.services())
    result = asyncio.run(
        validate_site(url, cfg, client, budget, output_dir=tmp_path, timing=clock.services())
    )
    return result, client, budget, clock, connector, resolver


def test_success_save_reload_and_in_process_identity_without_network(monkeypatch, tmp_path):
    result, client, budget, clock, connector, resolver = execute(monkeypatch, tmp_path, [wire()])
    assert result.status == "ok" and result.can_enter_step1
    root = result._run_directory
    site = SiteValidationResult.model_validate_json((root / "site.json").read_bytes())
    assert site == result
    contract = prepare_step1(result, budget=budget, timing=clock.services())
    assert contract.budget is budget and contract.fetch_result is result._fetch_result
    clock.seconds += 10
    loaded = load_step1_input(root, timing=clock.services())
    assert loaded.html == contract.html
    assert loaded.contract.site.allowed_hosts == result.allowed_hosts
    assert loaded.budget.remaining_seconds(step=1) <= 290
    assert loaded.budget.snapshot().total_attempts == 1
    assert len(connector.calls) == len(resolver.calls) == 1
    assert loaded.contract.crawl_permission == "unknown"


def test_tls_fallback_keeps_error_and_encoded_evidence(monkeypatch, tmp_path):
    payload = b"<html><p>Hello from HTTP</p></html>"
    result, _, _, clock, connector, _ = execute(
        monkeypatch,
        tmp_path,
        [
            [ssl.SSLCertVerificationError(1, "controlled")],
            wire(
                gzip.compress(payload, mtime=0),
                headers=b"Content-Type: text/html\r\nContent-Encoding: gzip\r\n",
            ),
        ],
    )
    assert result.status == "partial" and result.can_enter_step1 and result.fallback_used
    assert result.attempts[0].tls_state == "certificate_error"
    assert result.attempts[-1].tls_state == "not_applicable"
    assert "HTTPS_TLS_CERTIFICATE_ERROR" in result.reason_codes
    loaded = load_step1_input(result._run_directory, timing=clock.services())
    assert loaded.fetch_result.content_encoded == gzip.compress(payload, mtime=0)
    assert loaded.html == payload
    assert len(connector.calls) == 2


@pytest.mark.parametrize(
    "body,headers,state,eligible",
    [
        (b"<p>Hi</p>", b"Content-Type: text/html\r\n", "html", True),
        (
            b"<html><div id='root'></div><script src='/app.js'></script></html>",
            b"Content-Type: text/html\r\n",
            "js_candidate",
            True,
        ),
        (
            b"<html><title>Just a moment</title><div class='g-recaptcha' data-sitekey='x'></div>"
            b"<p>Verify you are human</p></html>",
            b"Content-Type: text/html\r\n",
            "access_barrier",
            False,
        ),
        (
            b"<html><title>Login</title><form><input type='password'></form></html>",
            b"Content-Type: text/html\r\n",
            "access_barrier",
            False,
        ),
        (
            b"<html><title>404 Not Found</title><p>Page not found</p></html>",
            b"Content-Type: text/html\r\n",
            "suspicious_html",
            True,
        ),
        (b"%PDF-1.7 binary", b"Content-Type: text/html\r\n", "non_html", False),
        (b"<html><p>Hello</p></html>", b"", "html", True),
        (b"<html><p>Hello</p></html>", b"Content-Type: image/png\r\n", "html", True),
        (
            b"<html><p>Bad \xff text</p></html>",
            b"Content-Type: text/html; charset=utf-8\r\n",
            "html",
            True,
        ),
    ],
)
def test_content_classification(monkeypatch, tmp_path, body, headers, state, eligible):
    result, _, _, clock, connector, _ = execute(
        monkeypatch, tmp_path, [wire(body, headers=headers)]
    )
    assert result.content_state == state and result.can_enter_step1 is eligible
    assert result.evidence[-1].classification_basis
    assert 0 <= result.evidence[-1].classification_confidence <= 1
    if b"\xff" in body:
        assert "CONTENT_DECODE_ERROR" in result.reason_codes
        assert result.status == "partial"
    if eligible:
        assert load_step1_input(result._run_directory, timing=clock.services()).html == body
    else:
        with pytest.raises(GeoAuditError):
            load_step1_input(result._run_directory, timing=clock.services())
    assert len(connector.calls) == 1


@pytest.mark.parametrize("declaration", [b"<![weird]>", b"<![unexpected[broken]]>"])
def test_malformed_html_declaration_is_saved_as_uncertain(monkeypatch, tmp_path, declaration):
    body = b"<html><body>" + declaration + b"<p>Business services</p></body></html>"
    result, _, _, clock, connector, _ = execute(monkeypatch, tmp_path, [wire(body)])
    assert result.status == "partial" and result.can_enter_step1
    assert result.content_state == "suspicious_html"
    assert "CONTENT_SUSPICIOUS" in result.reason_codes
    assert load_site_result(result._run_directory) == result
    assert load_step1_input(result._run_directory, timing=clock.services()).html == body
    assert len(connector.calls) == 1


@pytest.mark.parametrize(
    "status,state", [(204, "empty"), (403, "http_denied"), (404, "http_missing")]
)
def test_http_states_without_fallback(monkeypatch, tmp_path, status, state):
    result, _, _, _, connector, _ = execute(monkeypatch, tmp_path, [wire(b"", status=status)])
    assert result.content_state == state and not result.can_enter_step1
    assert not result.fallback_used and len(connector.calls) == 1


def test_login_barrier_does_not_invent_a_challenge(monkeypatch, tmp_path):
    result, _, budget, clock, connector, resolver = execute(
        monkeypatch,
        tmp_path,
        [wire(b"<html><title>Login</title><form><input type='password'></form></html>")],
    )
    assert result.status == "partial" and not result.can_enter_step1
    assert result.content_state == "access_barrier"
    assert result.reason_codes == ("ACCESS_LOGIN",)
    assert result.next_action == "resolve_access_barrier_or_choose_public_url"
    assert not result.fallback_used
    assert len(connector.calls) == len(resolver.calls) == budget.snapshot().total_attempts == 1
    saved = load_site_result(result._run_directory)
    assert saved.reason_codes == ("ACCESS_LOGIN",)
    with pytest.raises(GeoAuditError) as error:
        load_step1_input(result._run_directory, timing=clock.services())
    assert error.value.detail.code == "INPUT_ARTIFACT_INVALID"
    assert len(connector.calls) == 1


def test_external_redirect_requires_concrete_action(monkeypatch, tmp_path):
    result, _, _, clock, connector, _ = execute(
        monkeypatch,
        tmp_path,
        [wire(b"", status=302, headers=b"Location: https://other.com/landing?token=private\r\n")],
    )
    assert result.status == "needs_input" and not result.can_enter_step1
    assert "--allow-host other.com" in result.next_action and "token" not in result.next_action
    assert len(connector.calls) == 1
    assert load_site_result(result._run_directory).status == "needs_input"
    with pytest.raises(GeoAuditError):
        load_step1_input(result._run_directory, timing=clock.services())


def test_storage_failure_has_no_positive_commit(monkeypatch, tmp_path):
    original = EvidenceStore.write_blob

    def fail(*args, **kwargs):
        from geo_audit.errors import problem

        raise problem("OUTPUT_WRITE_FAILED", "output", "Controlled full disk")

    monkeypatch.setattr(EvidenceStore, "write_blob", fail)
    result, _, budget, _, connector, _ = execute(monkeypatch, tmp_path, [wire()])
    assert result.status == "failed" and not result.can_enter_step1
    assert "OUTPUT_WRITE_FAILED" in result.reason_codes
    assert budget.snapshot().total_attempts == len(connector.calls) == 1
    if (result._run_directory / "site.json").exists():
        assert (
            json.loads((result._run_directory / "site.json").read_bytes())["can_enter_step1"]
            is False
        )
    monkeypatch.setattr(EvidenceStore, "write_blob", original)


@pytest.mark.parametrize("seconds", [-1, 301], ids=["utc_rollback", "expired"])
def test_expired_and_unknown_version_rejected_before_body_open(monkeypatch, tmp_path, seconds):
    result, _, _, clock, _, _ = execute(monkeypatch, tmp_path, [wire()])
    root = result._run_directory
    reads = []
    monkeypatch.setattr(EvidenceStore, "verified_bytes", lambda *a, **kw: reads.append(True))
    clock.seconds = seconds
    with pytest.raises(GeoAuditError) as error:
        load_step1_input(root, timing=clock.services())
    assert error.value.detail.code == "INPUT_ARTIFACT_INVALID" and reads == []
    site = json.loads((root / "site.json").read_bytes())
    site["schema_version"] = "9000"
    (root / "site.json").write_text(json.dumps(site), encoding="utf-8")
    with pytest.raises(GeoAuditError):
        load_step1_input(root, timing=clock.services())
    assert reads == []


def test_corrupted_file_is_not_substituted(monkeypatch, tmp_path):
    result, _, _, clock, _, _ = execute(monkeypatch, tmp_path, [wire()])
    entity = next(f for f in result.evidence[-1].files if f.representation == "entity_bytes")
    (result._run_directory / entity.path).write_bytes(b"tampered")
    with pytest.raises(GeoAuditError) as error:
        load_step1_input(result._run_directory, timing=clock.services())
    assert error.value.detail.code == "INPUT_ARTIFACT_INVALID"


def test_programming_defect_is_not_site_unavailability(tmp_path):
    class Buggy:
        async def get(self, *_args, **_kwargs):
            raise RuntimeError("controlled programming bug")

    with pytest.raises(RuntimeError):
        asyncio.run(validate_site("example.com", Step0Config(), Buggy(), output_dir=tmp_path))
    assert not list(tmp_path.glob("*/site.json"))
