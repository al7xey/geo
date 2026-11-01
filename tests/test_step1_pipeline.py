"""End-to-end material/optional modes and an independent-process offline consumer."""

import asyncio
import json
import subprocess
import sys
from datetime import timedelta

import pytest

from geo_audit import GeoAuditError
from geo_audit.step1.handoff import load_step2_input, read_corpus
from geo_audit.step1.pipeline import run_step1
from geo_audit.step1.storage import digest, encoded_json
from tests.fakes import wire
from tests.test_step1_sitemap import fixture

BUSINESS = (
    "We manufacture precision metal components for industrial customers. "
    "Our machining team supplies custom shafts, bearings and engineering parts to factories. "
    "Production includes design review, certified materials, dimensional inspection and delivery. "
    "Customers order small batches and recurring production with documented technical drawings. "
    "Address: Berlin, Germany. We serve customers in Germany and France. "
    "Call +49 123 456 7890 for engineering support and quotations."
)


def html(text=BUSINESS, *, head="", links="", title="Manufacturing"):
    return (
        f'<html lang="en"><head><title>{title}</title>{head}</head>'
        f"<body><main><h1>Industrial components</h1><p>{text}</p></main>{links}</body></html>"
    ).encode()


def execute_pipeline(monkeypatch, tmp_path, *, entry=None, scripts=(), description=None, **config):
    runtime, site, clock, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [wire(b"User-agent: *\nAllow: /"), *scripts],
        entry=entry or html(),
        max_sitemap_files=0,
        **config,
    )
    result = asyncio.run(run_step1(runtime, description=description))
    return runtime, result, site, clock, connector


def test_useful_one_page_roundtrip_and_offline_freshness(monkeypatch, tmp_path):
    runtime, result, site, clock, connector = execute_pipeline(monkeypatch, tmp_path)
    assert result.crawl.material.state == "limited" and result.crawl.material.can_start_step2
    assert len(connector.calls) == result.run.budget.total_attempts == 2
    assert result.run.status == "ok"  # live headers are retained without another entry GET
    assert "ENABLED_DIAGNOSTIC_UNAVAILABLE" not in result.run.limitations
    assert [d.reason_code for d in result.checks.diagnostics] == ["CONFIG_DISABLED"] * 2
    handoff, corpus = read_corpus(runtime.store.io.root, now=clock.utcnow())
    assert handoff.result == result and BUSINESS in next(iter(corpus.values()))
    assert handoff.source_commit_sha256 == digest((runtime.store.io.root / "run.json").read_bytes())
    with pytest.raises(GeoAuditError):
        load_step2_input(runtime.store.io.root, now=clock.utcnow() + timedelta(days=8))
    # Crawl deadline is300s; offline consumer remains valid the next day.
    assert load_step2_input(runtime.store.io.root, now=clock.utcnow() + timedelta(days=1))


def test_three_legal_pages_do_not_create_business_material(monkeypatch, tmp_path):
    entry = html(
        "Terms and conditions. " * 60,
        title="Legal terms",
        links='<a href="/ru/privacy">Privacy</a><a href="/ru/terms">Terms</a>',
    )
    runtime, result, _, _, connector = execute_pipeline(
        monkeypatch,
        tmp_path,
        entry=entry,
        scripts=[
            wire(html("Privacy policy. " * 80, title="Privacy")),
            wire(html("Terms and conditions. " * 80, title="Terms")),
        ],
    )
    assert result.crawl.material.state == "insufficient"
    assert not result.crawl.material.can_start_step2
    assert result.run.status == "needs_input"
    assert result.crawl.coverage.unique_html_pages == 3
    assert len(connector.calls) == result.run.budget.total_attempts == 4


def test_three_business_pages_are_usable_and_query_slots_are_not_retries(monkeypatch, tmp_path):
    runtime, result, _, clock, connector = execute_pipeline(
        monkeypatch,
        tmp_path,
        entry=html(links='<a href="/ru/about">About</a><a href="/ru/services">Services</a>'),
        scripts=[wire(html()), wire(html())],
    )
    assert result.crawl.material.state == "usable"
    assert len(result.crawl.material.useful_page_ids) == 3
    assert result.crawl.coverage.extracted_pages == 3
    assert len(connector.calls) == 4
    assert len(load_step2_input(runtime.store.io.root, now=clock.utcnow()).useful_texts) == 3


def test_user_description_provenance_does_not_fix_robots_failure(monkeypatch, tmp_path):
    runtime, _, clock, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [wire(b"User-agent: *\nDisallow: /")],
        entry=html("Hello"),
        max_sitemap_files=0,
    )
    result = asyncio.run(run_step1(runtime, description=BUSINESS))
    assert result.crawl.material.state == "user_supported"
    user = result.crawl.material.user_materials[0]
    assert user.source_type == "user_provided" and user.material_id.startswith("user-")
    assert any(c.kind == "robots_policy" and c.status == "fail" for c in result.checks.checks)
    assert len(connector.calls) == 2
    assert load_step2_input(runtime.store.io.root, now=clock.utcnow())


def test_enabled_browser_is_honest_unknown_and_never_launches(monkeypatch, tmp_path):
    _, result, _, _, connector = execute_pipeline(monkeypatch, tmp_path, browser_enabled=True)
    browser = next(d for d in result.checks.diagnostics if d.kind == "browser")
    assert browser.status == "unknown" and browser.reason_code == "SAFE_EGRESS_UNAVAILABLE"
    assert "ENABLED_DIAGNOSTIC_UNAVAILABLE" in result.run.limitations
    assert result.run.browser_attempts == 0 and len(connector.calls) == 2


def test_directives_multivalue_addressed_geo_and_no_external_canonical_get(monkeypatch, tmp_path):
    entry = html(links='<a href="/ru/services">Service</a>')
    headers = (
        b"Content-Type: text/html\r\nX-Robots-Tag: googlebot: index\r\n"
        b"X-Robots-Tag: googlebot: noindex, nosnippet\r\n"
        b"X-Robots-Tag: UnknownBot: index, noindex\r\n"
    )
    page = html(
        head='<meta name="robots" content="index">'
        '<link rel="canonical" href="https://outside.example/">'
        '<link rel="alternate" hreflang="de" href="/de/">'
    )
    _, result, _, _, connector = execute_pipeline(
        monkeypatch, tmp_path, entry=entry, scripts=[wire(page, headers=headers)]
    )
    service = result.crawl.pages[1]
    captured = [d for d in service.directives if d.source == "http_header"]
    assert len(captured) == 3
    assert any(
        c.status == "fail" and c.reason_code == "NOINDEX_OBSERVED" for c in result.checks.checks
    )
    assert any(c.status == "unknown" and c.robot_id == "unknownbot" for c in result.checks.checks)
    assert {s.kind for s in service.signals} >= {
        "lang",
        "hreflang",
        "address",
        "service_geography",
        "phone",
        "tld",
    }
    assert all(s.strength == "weak" for s in service.signals if s.kind in {"phone", "tld"})
    assert not any(b"outside.example" in b"".join(s.writes) for s in connector.streams)


def test_offline_consumer_new_process_has_no_dns_sockets_or_extractor(monkeypatch, tmp_path):
    runtime, _, _, clock, _ = execute_pipeline(monkeypatch, tmp_path)
    program = r"""
import socket,sys
from datetime import datetime
from pathlib import Path
def forbidden(*a,**k): raise AssertionError('offline consumer attempted network/extraction')
socket.getaddrinfo = socket.create_connection = socket.socket.connect = forbidden
import trafilatura
trafilatura.extract = forbidden
from geo_audit.step1.handoff import read_corpus
handoff,texts=read_corpus(Path(sys.argv[1]),now=datetime.fromisoformat(sys.argv[2]))
assert handoff.result.crawl.material.can_start_step2 and texts
print('offline verified')
"""
    completed = subprocess.run(
        [sys.executable, "-c", program, str(runtime.store.io.root), clock.utcnow().isoformat()],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "offline verified" in completed.stdout


@pytest.mark.parametrize("damage", ["evidence", "version", "quote"])
def test_offline_rejects_corrupt_artifacts_and_exact_quote_slice(monkeypatch, tmp_path, damage):
    runtime, result, _, clock, _ = execute_pipeline(monkeypatch, tmp_path)
    root = runtime.store.io.root
    if damage == "evidence":
        text = next(e for e in result.evidence if e.representation == "main_text")
        (root / text.file.path).write_bytes(b"tampered")
    elif damage == "version":
        data = json.loads((root / "run.json").read_bytes())
        data["schema_version"] = "step1-999"
        (root / "run.json").write_bytes(encoded_json(data))
    else:
        data = json.loads((root / "crawl.json").read_bytes())
        quote = data["pages"][0]["business_quotes"][0]
        quote["text"] = "X" * len(quote["text"])
        body = encoded_json(data)
        (root / "crawl.json").write_bytes(body)
        commit = json.loads((root / "run.json").read_bytes())
        next(a for a in commit["artifact_hashes"] if a["name"] == "crawl.json")["sha256"] = digest(
            body
        )
        (root / "run.json").write_bytes(encoded_json(commit))
    with pytest.raises(GeoAuditError):
        load_step2_input(root, now=clock.utcnow())
