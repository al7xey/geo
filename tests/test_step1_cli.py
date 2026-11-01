"""CLI boundaries, no leaked skip URLs, and strictly offline saved-corpus validation."""

import json

from geo_audit.cli import main, parser
from geo_audit.step1.handoff import load_step2_input
from tests.test_step1_pipeline import execute_pipeline


def test_additive_commands_preserve_old_cli():
    for command in ("audit", "step1", "load-step2", "step0", "load-step1"):
        assert command in parser().format_help()


def test_description_rejected_before_first_get(monkeypatch, capsys):
    import geo_audit.step1.pipeline as pipeline

    async def forbidden(*a, **k):
        raise AssertionError("unexpected network")

    monkeypatch.setattr(pipeline, "validate_site", forbidden)
    assert main(["audit", "https://example.com/?secret=private", "--description", " "]) == 2
    output = capsys.readouterr()
    assert "private" not in output.out + output.err


def test_offline_cli_summary_uses_verified_corpus(monkeypatch, tmp_path, capsys):
    import geo_audit.step1.cli as cli

    runtime, result, _, clock, _ = execute_pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "load_step2_input", lambda p: load_step2_input(p, now=clock.utcnow()))
    assert main(["load-step2", str(runtime.store.io.root)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["material"] == "limited" and output["can_start_step2"]
    assert "secret" not in json.dumps(output)


def test_unknown_version_cli_exit_code_and_safe_error(tmp_path, capsys):
    directory = tmp_path / "unknown"
    directory.mkdir()
    (directory / "run.json").write_text('{"schema_version":"secret-unknown"}')
    assert main(["load-step2", str(directory)]) == 2
    assert "secret-unknown" not in capsys.readouterr().err


def test_extractor_does_not_fetch_embedded_external_entities(monkeypatch, tmp_path):
    import socket

    import trafilatura

    from geo_audit.step1.crawler import Crawler
    from geo_audit.step1.extraction import extract_page
    from geo_audit.step1.models import DiscoverySource
    from tests.test_step1_pipeline import html
    from tests.test_step1_sitemap import fixture

    runtime, _, _, _, _ = fixture(monkeypatch, tmp_path, [], entry=html(), max_sitemap_files=0)

    def forbidden(*a, **k):
        raise AssertionError("extractor requested an external resource")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(trafilatura, "fetch_url", forbidden)
    crawler = Crawler(runtime)
    url = runtime.handoff.contract.site.effective_url
    crawler.admit(
        url,
        DiscoverySource(kind="entry", source_id=runtime.store.entry_evidence_id, occurrence=0),
        entry=True,
    )
    page = crawler.page(
        crawler.candidates[url],
        url,
        runtime.store.entry_evidence_id,
        "html",
        complete_headers=False,
    )
    malicious = '<!DOCTYPE html [<!ENTITY x SYSTEM "http://127.0.0.1/secret">]>' + html().decode()
    extracted = extract_page(runtime, page, malicious, ())
    assert extracted.main_text is not None
