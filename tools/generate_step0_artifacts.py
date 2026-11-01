"""Generate deterministic schemas, field reference and SYNTHETIC contract examples.

Does not import document builders, change PDFs or make network calls.
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from geo_audit import (
    BudgetSnapshot,
    EvidenceRecord,
    FetchAttempt,
    SiteValidationResult,
    Step0Config,
    Step1Input,
)
from geo_audit.errors import ErrorDetail
from geo_audit.models import EvidenceFile, Header, RedirectHop, ResolvedTarget
from geo_audit.policy import next_action_for
from geo_audit.schemas import MODELS, schema_for
from geo_audit.urls import normalize_input

ROOT = Path(__file__).resolve().parents[1]
STAMP = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
HTML = b"<!doctype html><html><title>SYNTHETIC FIXTURE</title><p>Example only.</p></html>"


def document(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def artifacts():
    config = Step0Config()
    budget = BudgetSnapshot(
        schema_version="1.2.0",
        started_at=STAMP,
        captured_at=STAMP + timedelta(seconds=1),
        step0_deadline_at=STAMP + timedelta(seconds=60),
        total_deadline_at=STAMP + timedelta(seconds=300),
        elapsed_seconds=1.0,
        step0_attempts=1,
        total_attempts=1,
        step0_max_attempts=15,
        total_max_attempts=150,
    )
    blob = EvidenceFile(
        path="raw/entry.html",
        sha256=hashlib.sha256(HTML).hexdigest(),
        size_bytes=len(HTML),
        representation="entity_bytes",
        complete=True,
    )
    evidence = EvidenceRecord(
        schema_version="1.2.0",
        evidence_id="fixture-ev-01",
        attempt_id="fixture-req-01",
        requested_url="https://example.com/ru/?city=msk",
        captured_at=STAMP,
        files=(blob,),
        declared_encoding="utf-8",
        applied_encoding="utf-8",
        replacement_characters=0,
        sniffed_html=False,
        classification_basis=(
            "Synthetic fixture: explicitly authored HTML, no network observation.",
        ),
        classification_confidence=1.0,
        redacted_headers=("set-cookie",),
    )
    attempt = FetchAttempt(
        schema_version="1.2.0",
        attempt_id="fixture-req-01",
        parent_attempt_id=None,
        requested_url=evidence.requested_url,
        purpose="initial",
        request_sent=True,
        resolved_target=ResolvedTarget(
            host="example.com",
            port=443,
            addresses=("93.184.216.34",),
            connected_ip="93.184.216.34",
            enforcement="pinned",
        ),
        status_code=200,
        response_headers=(Header(name="content-type", value="text/html", redacted=False),),
        started_at=STAMP,
        elapsed_ms=100.0,
        transport_error=None,
        tls_state="verified",
        evidence_id=evidence.evidence_id,
        bytes_read=len(HTML),
        decoded_bytes=len(HTML),
        body_complete=True,
    )
    observation_bytes = attempt.model_dump_json().encode("utf-8")
    extra_blobs = {
        "encoded": (HTML, "content_encoded"),
        "text": (HTML, "decoded_text"),
        "attempt": (observation_bytes, "attempt_observation"),
    }
    evidence = evidence.model_copy(
        update={
            "files": (
                blob,
                *(
                    EvidenceFile.model_validate(
                        {
                            "path": f"raw/{name}.bin",
                            "sha256": hashlib.sha256(data).hexdigest(),
                            "size_bytes": len(data),
                            "representation": representation,
                            "complete": True,
                        }
                    )
                    for name, (data, representation) in extra_blobs.items()
                ),
            )
        }
    )
    site = SiteValidationResult(
        schema_version="1.2.0",
        algorithm_version="step0-1.2",
        config_version=config.config_version,
        config=config,
        config_sha256=config.fingerprint(),
        run_id="fixture-run-ok",
        parent_run_id=None,
        started_at=STAMP,
        finished_at=budget.captured_at,
        source_timezone="Europe/Moscow",
        input_url="example.com/ru/?city=msk#prices",
        normalized_url=evidence.requested_url,
        input_scheme_explicit=False,
        input_host_unicode="example.com",
        effective_url=evidence.requested_url,
        origin="https://example.com",
        entry_path="/ru/",
        allowed_hosts=("example.com",),
        path_scope=(),
        status="ok",
        can_enter_step1=True,
        content_state="html",
        diagnostic_allowed=False,
        fallback_used=False,
        selected_evidence_id=evidence.evidence_id,
        final_attempt_id=attempt.attempt_id,
        attempt_ids=(attempt.attempt_id,),
        attempts=(attempt,),
        redirect_chain=(),
        evidence=(evidence,),
        errors=(),
        reason_codes=(),
        proposed_target_url=None,
        next_action="continue_step1",
        budget=budget,
    )
    step1 = Step1Input(
        schema_version="1.2.0",
        source_step=0,
        site=site,
        selected_response=attempt,
        selected_evidence=evidence,
        budget=budget,
        crawl_permission="unknown",
    )
    examples = {
        "config": config,
        "site-ok": site,
        "fetch-attempt": attempt,
        "evidence": evidence,
        "budget": budget,
        "step1-input": step1,
    }

    def case(name, updates):
        data = site.model_dump(mode="json")
        data.update(updates)
        data["next_action"] = next_action_for(
            status=data["status"],
            content_state=data["content_state"],
            reason_codes=tuple(data["reason_codes"]),
            can_enter_step1=data["can_enter_step1"],
            proposed_host=normalize_input(data["proposed_target_url"]).host_ascii
            if data["proposed_target_url"]
            else None,
        )
        data["run_id"] = "fixture-" + name
        result = SiteValidationResult.model_validate_json(json.dumps(data))
        examples[name] = result
        return result

    def error(code, category, attempt_id=None):
        return ErrorDetail(
            code=code,
            category=category,
            message="Synthetic fixture reason.",
            retryable=False,
            attempt_id=attempt_id,
        ).model_dump(mode="json")

    for state, code, eligible in [
        ("js_candidate", "CONTENT_JS_CANDIDATE", True),
        ("suspicious_html", "CONTENT_SUSPICIOUS", True),
        ("suspicious_html", "CONTENT_SUSPICIOUS", False),
        ("access_barrier", "ACCESS_CHALLENGE", False),
        ("non_html", "CONTENT_NON_HTML", False),
    ]:
        updates = {
            "status": "partial",
            "content_state": state,
            "can_enter_step1": eligible,
            "diagnostic_allowed": state == "suspicious_html" and eligible,
            "reason_codes": [code],
            "errors": [error(code, "content")],
        }
        if not eligible:
            updates.update(selected_evidence_id=None, effective_url=None, origin=None)
        case(f"partial-{state}-{'enter' if eligible else 'stop'}", updates)

    denied_attempt = attempt.model_dump(mode="json")
    denied_attempt["status_code"] = 403
    case(
        "failed-http-403",
        {
            "attempts": [denied_attempt],
            "status": "failed",
            "can_enter_step1": False,
            "content_state": "http_denied",
            "selected_evidence_id": None,
            "effective_url": None,
            "origin": None,
            "reason_codes": ["HTTP_DENIED"],
            "errors": [error("HTTP_DENIED", "http")],
        },
    )
    invalid_budget = budget.model_dump(mode="json")
    invalid_budget.update(step0_attempts=0, total_attempts=0)
    case(
        "failed-input",
        {
            "input_url": "ftp://example.com",
            "normalized_url": None,
            "input_scheme_explicit": None,
            "input_host_unicode": None,
            "effective_url": None,
            "origin": None,
            "entry_path": None,
            "allowed_hosts": [],
            "status": "failed",
            "can_enter_step1": False,
            "content_state": "not_received",
            "selected_evidence_id": None,
            "final_attempt_id": None,
            "attempt_ids": [],
            "attempts": [],
            "evidence": [],
            "budget": invalid_budget,
            "errors": [error("INPUT_SCHEME_UNSUPPORTED", "input")],
            "reason_codes": ["INPUT_SCHEME_UNSUPPORTED"],
        },
    )
    hop = RedirectHop(
        schema_version="1.2.0",
        source_attempt_id=attempt.attempt_id,
        source_url=attempt.requested_url,
        status_code=302,
        raw_location="https://other.example/",
        target_url="https://other.example/",
        decision="needs_input",
        reason_code="REDIRECT_EXTERNAL_HOST",
        observed_at=STAMP,
        psl_sha256=None,
    )
    redirected_attempt = attempt.model_dump(mode="json")
    redirected_attempt["status_code"] = 302
    redirected_attempt["response_headers"] = [
        {"name": "location", "value": hop.raw_location, "redacted": True}
    ]
    case(
        "needs-input",
        {
            "attempts": [redirected_attempt],
            "redirect_chain": [hop.model_dump(mode="json")],
            "status": "needs_input",
            "can_enter_step1": False,
            "content_state": "http_other",
            "selected_evidence_id": None,
            "effective_url": None,
            "origin": None,
            "reason_codes": ["REDIRECT_EXTERNAL_HOST"],
            "errors": [error("REDIRECT_EXTERNAL_HOST", "http")],
            "proposed_target_url": hop.target_url,
            "next_action": "rerun step0 with --allow-host other.example; no target GET was sent",
        },
    )
    examples["redirect-hop"] = hop
    tls_error = error("HTTPS_TLS_CERTIFICATE_ERROR", "transport", attempt.attempt_id)
    tls_attempt = attempt.model_dump(mode="json")
    tls_attempt.update(
        status_code=None,
        response_headers=[],
        transport_error=tls_error,
        tls_state="certificate_error",
        bytes_read=0,
        decoded_bytes=0,
        body_complete=False,
    )
    observation = document({"synthetic": True, "error": tls_error})
    observation_file = EvidenceFile(
        path="raw/tls-observation.json",
        sha256=hashlib.sha256(observation).hexdigest(),
        size_bytes=len(observation),
        representation="attempt_observation",
        complete=True,
    )
    tls_evidence = evidence.model_dump(mode="json")
    tls_evidence.update(
        files=[observation_file.model_dump(mode="json")],
        declared_encoding=None,
        applied_encoding=None,
    )
    case(
        "failed-tls",
        {
            "attempts": [tls_attempt],
            "evidence": [tls_evidence],
            "status": "failed",
            "can_enter_step1": False,
            "content_state": "not_received",
            "selected_evidence_id": None,
            "effective_url": None,
            "origin": None,
            "errors": [tls_error],
            "reason_codes": ["HTTPS_TLS_CERTIFICATE_ERROR"],
        },
    )
    http_attempt = attempt.model_dump(mode="json")
    http_attempt.update(
        attempt_id="fixture-req-02",
        parent_attempt_id=attempt.attempt_id,
        requested_url="http://example.com/ru/?city=msk",
        purpose="http_fallback",
        tls_state="not_applicable",
        evidence_id="fixture-ev-02",
    )
    http_attempt["resolved_target"]["port"] = 80
    http_evidence = evidence.model_dump(mode="json")
    http_evidence.update(
        evidence_id="fixture-ev-02",
        attempt_id="fixture-req-02",
        requested_url=http_attempt["requested_url"],
    )
    http_budget = budget.model_dump(mode="json")
    http_budget.update(step0_attempts=2, total_attempts=2)
    case(
        "partial-fallback",
        {
            "attempts": [tls_attempt, http_attempt],
            "attempt_ids": [attempt.attempt_id, "fixture-req-02"],
            "evidence": [tls_evidence, http_evidence],
            "status": "partial",
            "fallback_used": True,
            "selected_evidence_id": "fixture-ev-02",
            "final_attempt_id": "fixture-req-02",
            "effective_url": http_attempt["requested_url"],
            "origin": "http://example.com",
            "budget": http_budget,
            "reason_codes": ["HTTPS_TLS_CERTIFICATE_ERROR"],
            "errors": [tls_error],
        },
    )
    examples["error"] = ErrorDetail.model_validate_json(json.dumps(tls_error))
    result = {
        Path("schemas") / f"{name}.schema.json": document(schema_for(name)) for name in MODELS
    }
    result.update(
        {
            Path("examples/contracts") / f"{name}.json": document(value.model_dump(mode="json"))
            for name, value in examples.items()
        }
    )
    result[Path("examples/contracts/run/raw/entry.html")] = HTML
    for name, (data, _) in extra_blobs.items():
        result[Path(f"examples/contracts/run/raw/{name}.bin")] = data
    result[Path("examples/contracts/run/raw/tls-observation.json")] = observation
    lines = [
        "# Поля контрактов шага 0",
        "",
        "Сгенерировано из Pydantic-моделей. Не редактировать вручную.",
        "Все выходные поля обязательны, включая nullable; у конфигурации есть defaults.",
        "`null` означает неприменимость или отсутствие наблюдения, а не успех проверки.",
        "JSON Schema описывает форму; межполевые инварианты дополнительно проверяет Python.",
        "",
    ]
    documented = set()
    for name in MODELS:
        schema = schema_for(name)
        sections = {name: schema, **schema.get("$defs", {})}
        for title, section in sections.items():
            if "properties" not in section or title in documented:
                continue
            documented.add(title)
            lines += [
                f"## {name} / {title}",
                "",
                "| Поле | Обязательно | null | Тип / ограничения |",
                "|---|---|---|---|",
            ]
            for field, definition in section["properties"].items():
                nullable = any(s.get("type") == "null" for s in definition.get("anyOf", []))
                type_spec = {
                    k: v for k, v in definition.items() if k not in {"title", "description"}
                }
                encoded = json.dumps(type_spec, ensure_ascii=False).replace("|", "&#124;")
                lines.append(
                    f"| {field} | {'да' if field in section.get('required', []) else 'нет'} | "
                    f"{'да' if nullable else 'нет'} | `{encoded}` |"
                )
            lines.append("")
    result[Path("docs/implementation/STEP_0_FIELDS.md")] = "\n".join(lines).encode("utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Compare generated bytes without writing"
    )
    args = parser.parse_args()
    expected = artifacts()
    stale = []
    for relative, data in expected.items():
        target = ROOT / relative
        if args.check:
            if not target.is_file() or target.read_bytes() != data:
                stale.append(str(relative))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    if stale:
        raise SystemExit("Stale/missing artifacts: " + ", ".join(stale))
    print(
        f"{'Checked' if args.check else 'Generated'} {len(expected)} artifacts; no network calls."
    )


if __name__ == "__main__":
    main()
