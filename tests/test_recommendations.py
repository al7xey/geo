"""Recommendations are reproducible, offline, evidence-linked technical guidance."""

import json
import socket

import pytest
from pydantic import ValidationError

from geo_audit import GeoAuditError
from geo_audit.cli import main
from geo_audit.recommendations import (
    RecommendationReport,
    recommendation_report,
    validate_recommendations,
)
from tests.test_step1_pipeline import execute_pipeline, html


def no_network(*args, **kwargs):
    raise AssertionError("offline recommendations attempted network")


def test_recommendations_offline_reproducible_reference_checks(monkeypatch, tmp_path, capsys):
    runtime, result, site, _, connector = execute_pipeline(
        monkeypatch, tmp_path, entry=html(head='<meta name="robots" content="noindex">')
    )
    calls = len(connector.calls)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    root = runtime.store.io.root
    report = recommendation_report(root)
    assert report.source_step == 1
    assert report.scope == "technical_only_no_ai_visibility_measurement"
    indexing = next(r for r in report.recommendations if r.category == "indexing")
    assert indexing.check_ids and indexing.evidence_ids
    checks = {c.check_id for c in result.checks.checks}
    evidence = {e.evidence_id for e in result.evidence}
    assert all(set(r.check_ids) <= checks for r in report.recommendations)
    assert all(set(r.evidence_ids) <= evidence for r in report.recommendations)
    assert not any(r.category in {"javascript", "ua"} for r in report.recommendations)
    assert validate_recommendations(report, root) == report
    assert RecommendationReport.model_validate_json(report.model_dump_json()) == report
    assert main(["recommend", str(root)]) == 0
    assert json.loads(capsys.readouterr().out) == report.model_dump(mode="json")
    assert recommendation_report(site._run_directory).source_step == 0
    assert len(connector.calls) == calls
    altered = report.model_copy(update={"source_commit_sha256": "0" * 64})
    with pytest.raises(GeoAuditError):
        validate_recommendations(altered, root)
    with pytest.raises(ValidationError):
        RecommendationReport.model_validate({**report.model_dump(), "schema_version": "future"})
    evidence_file = root / result.evidence[0].file.path
    evidence_file.write_bytes(b"tampered")
    with pytest.raises(GeoAuditError):
        recommendation_report(root)


def test_long_service_text_produces_material_guidance(monkeypatch, tmp_path):
    runtime, result, _, _, _ = execute_pipeline(
        monkeypatch, tmp_path, entry=html("Terms and conditions. " * 100, title="Legal terms")
    )
    assert not result.crawl.material.can_start_step2
    report = recommendation_report(runtime.store.io.root)
    assert any(r.category == "material" for r in report.recommendations)
    assert len(report.recommendations) <= 32
