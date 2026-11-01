"""Boundary tests for extraction, mode gates and common-budget HTTPS/UA diagnostics."""

import asyncio

import pytest

from geo_audit import GeoAuditError, Step0Config
from geo_audit.handoff import prepare_step1
from geo_audit.step1 import Step1Config, Step1Runtime, load_step1_result
from geo_audit.step1.diagnostics import rendered_gain
from geo_audit.step1.handoff import load_step2_input
from geo_audit.step1.material import description_input
from geo_audit.step1.pipeline import run_step1
from tests.fakes import wire
from tests.test_step0_end_to_end import execute
from tests.test_step1_pipeline import BUSINESS, execute_pipeline, html
from tests.test_step1_sitemap import fixture, requests


@pytest.mark.parametrize(
    "static,rendered,expected",
    [
        (0, 499, "no_material_gain"),
        (0, 500, "observed_gain"),
        (400, 899, "no_material_gain"),
        (400, 900, "observed_gain"),
        (600, 1100, "no_material_gain"),
        (600, 1200, "observed_gain"),
        (1000, 3000, "no_material_gain"),
    ],
)
def test_gain_boundaries_without_claiming_browser_integration(static, rendered, expected):
    assert rendered_gain(static, rendered) == expected
    assert rendered_gain(static, rendered, complete=False) == "inconclusive"


def test_ua_status_type_and_content_comparison_and_no_extended_probe(monkeypatch, tmp_path):
    _, result, _, _, connector = execute_pipeline(
        monkeypatch,
        tmp_path,
        ua_probes_enabled=True,
        scripts=[
            wire(html(), status=403),
            wire(html()),
            wire(html("An unrelated news story. " * 50)),
        ],
    )
    probes = [d for d in result.checks.diagnostics if d.kind == "ua_probe" and d.url]
    assert [p.status for p in probes] == ["warning", "pass", "warning"]
    assert len(connector.calls) == result.run.budget.total_attempts == 5
    writes = [b"".join(s.writes) for s in connector.streams]
    assert b"User-Agent: GEOAudit/0.1" in writes[0]
    assert all(b"Google-Extended" not in w for w in writes)
    assert any(
        d.reason_code == "NO_HTTP_USER_AGENT" and d.status == "not_applicable"
        for d in result.checks.diagnostics
    )


def test_ua_probes_respect_own_and_addressed_rep_before_get(monkeypatch, tmp_path):
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [
            wire(b"User-agent: *\nAllow: /\nUser-agent: GPTBot\nDisallow: /\n"),
            wire(html()),
            wire(html()),
        ],
        entry=html(),
        max_sitemap_files=0,
        ua_probes_enabled=True,
    )
    result = asyncio.run(run_step1(runtime))
    assert len(connector.calls) == 4
    assert not any(b" GPTBot/" in b"".join(s.writes) for s in connector.streams)
    assert any(d.reason_code == "ROBOTS_FORBIDS" for d in result.checks.diagnostics)


def test_explicit_http_diagnostic_robots_and_tls403_separate_from_corpus(monkeypatch, tmp_path):
    config = Step0Config()
    site, client, budget, clock, connector, _ = execute(
        monkeypatch,
        tmp_path / "source",
        [
            wire(html()),
            wire(b"User-agent: *\nAllow: /"),
            wire(b"User-agent: *\nAllow: /"),
            wire(b"Denied", status=403),
        ],
        url="http://example.com/ru/",
        config=config,
    )
    runtime = Step1Runtime(
        prepare_step1(site, budget=budget, timing=clock.services()),
        source_directory=site._run_directory,
        output_dir=tmp_path / "out",
        client=client,
        timing=clock.services(),
        config=Step1Config(source_step0_config_sha256=site.config_sha256, max_sitemap_files=0),
    )
    result = asyncio.run(run_step1(runtime))
    diag = next(d for d in result.checks.diagnostics if d.kind == "https_alternative")
    assert diag.status == "pass" and diag.reason_code == "TLS_VERIFIED"
    assert any(
        c.reason_code == "HTTPS_DIAGNOSTIC_HTTP_ACCESS" and c.status == "fail"
        for c in result.checks.checks
    )
    assert len(connector.calls) == result.run.budget.total_attempts == 4
    assert requests(connector)[-2:] == [b"GET /robots.txt HTTP/1.1", b"GET /ru/ HTTP/1.1"]
    assert all(p.effective_url.startswith("http:") for p in result.crawl.pages)


def test_extraction_fallback_tables_unicode_and_exact_quotes(monkeypatch, tmp_path):
    import trafilatura

    monkeypatch.setattr(trafilatura, "extract", lambda *a, **k: "Navigation")
    entry = html(
        BUSINESS + " Cafe\u0301", links="<table><tr><td>Size</td><td>Price</td></tr></table>"
    )
    runtime, result, _, clock, _ = execute_pipeline(monkeypatch, tmp_path, entry=entry)
    page = result.crawl.pages[0]
    assert page.extraction_method == "beautifulsoup"
    assert page.alternate_texts and "EXTRACTORS_DISAGREE" in page.limitations
    assert load_step2_input(runtime.store.io.root, now=clock.utcnow())
    assert page.business_quotes[0].text.startswith("We manufacture")


def test_programming_extractor_exception_is_not_suppressed(monkeypatch, tmp_path):
    import trafilatura

    def bug(*a, **k):
        raise AssertionError("extractor implementation bug")

    monkeypatch.setattr(trafilatura, "extract", bug)
    runtime, _, _, connector, _ = fixture(
        monkeypatch, tmp_path, [], entry=html(), max_sitemap_files=0
    )
    with pytest.raises(AssertionError, match="implementation bug"):
        asyncio.run(run_step1(runtime))
    saved = load_step1_result(runtime.store.io.root)
    assert saved.run.status == "failed" and len(connector.calls) == 1
    assert any(e.representation == "decoded_text" for e in saved.evidence)


def test_description_file_bounds_encoding_and_empty_input(tmp_path):
    path = tmp_path / "description.txt"
    path.write_bytes(b"x" * 65537)
    with pytest.raises(GeoAuditError):
        description_input(file=path)
    path.write_bytes(b"\xff")
    with pytest.raises(GeoAuditError):
        description_input(file=path)
    with pytest.raises(GeoAuditError):
        description_input(text="   ")
    path.write_text(BUSINESS, encoding="utf8")
    assert description_input(file=path) == BUSINESS


def test_thirty_page_cap_fifty_links_and_no_extra_entry_get(monkeypatch, tmp_path):
    entry = html(links="".join(f'<a href="/ru/p{n}">Page {n}</a>' for n in range(50)))
    _, result, _, _, connector = execute_pipeline(
        monkeypatch, tmp_path, entry=entry, scripts=[wire(html())] * 29
    )
    assert result.crawl.coverage.selected_candidates == 30
    assert result.crawl.coverage.unique_html_pages == 30
    assert sum(c.state == "not_selected_limit" for c in result.crawl.candidates) == 21
    assert len(connector.calls) == result.run.budget.total_attempts == 31
    assert sum(b"GET /ru/?secret=local " in r for r in requests(connector)) == 1


def test_saved_legacy_headers_stay_unknown_without_entry_refetch(monkeypatch, tmp_path):
    runtime, site, clock, connector, _ = fixture(
        monkeypatch, tmp_path, [wire(b"User-agent: *\nAllow: /")], entry=html(), max_sitemap_files=0
    )
    saved = Step1Runtime.from_directory(
        site._run_directory,
        output_dir=tmp_path / "saved",
        client=runtime.client,
        timing=clock.services(),
        config=runtime.config,
    )
    result = asyncio.run(run_step1(saved))
    assert result.run.status == "partial" and "LEGACY_HEADERS_INCOMPLETE" in result.run.limitations
    assert any(
        c.reason_code == "OBSERVATION_INCOMPLETE" and c.status == "unknown"
        for c in result.checks.checks
    )
    assert len(connector.calls) == 2


@pytest.mark.parametrize("case", ["robots_unknown", "nonstandard_port"])
def test_https_diagnostic_never_ignores_robots_or_guesses_ports(monkeypatch, tmp_path, case):
    config = Step0Config(allowed_extra_ports=(8888,) if case == "nonstandard_port" else ())
    scripts = [wire(html()), wire(b"User-agent: *\nAllow: /")]
    if case == "robots_unknown":
        scripts.extend([wire(b"", status=503)] * 3)
    url = "http://example.com:8888/ru/" if case == "nonstandard_port" else "http://example.com/ru/"
    site, client, budget, clock, connector, _ = execute(
        monkeypatch, tmp_path / "source", scripts, url=url, config=config
    )
    runtime = Step1Runtime(
        prepare_step1(site, budget=budget, timing=clock.services()),
        source_directory=site._run_directory,
        output_dir=tmp_path / "out",
        client=client,
        timing=clock.services(),
        config=Step1Config(source_step0_config_sha256=site.config_sha256, max_sitemap_files=0),
    )
    result = asyncio.run(run_step1(runtime))
    diag = next(d for d in result.checks.diagnostics if d.kind == "https_alternative")
    assert diag.status == "unknown"
    if case == "nonstandard_port":
        assert diag.reason_code == "NONSTANDARD_PORT_MAPPING_REQUIRED"
        assert len(connector.calls) == 2 and all(port == 8888 for _, port in connector.calls)
    else:
        assert len(connector.calls) == 5
        assert requests(connector)[-3:] == [b"GET /robots.txt HTTP/1.1"] * 3


def test_probe_caps_three_pages_times_three_agents(monkeypatch, tmp_path):
    _, result, _, _, connector = execute_pipeline(
        monkeypatch,
        tmp_path,
        entry=html(links="".join(f'<a href="/ru/p{n}">Page</a>' for n in range(3))),
        scripts=[wire(html())] * 12,
        ua_probes_enabled=True,
    )
    assert result.crawl.coverage.unique_html_pages == 4
    assert len([d for d in result.checks.diagnostics if d.kind == "ua_probe" and d.url]) == 9
    assert result.run.budget.total_attempts == len(connector.calls) == 14


def test_preflight_rejects_node_bomb_before_dom_allocations(monkeypatch, tmp_path):
    import geo_audit.step1.extraction as extraction
    from geo_audit.step1.crawler import Crawler
    from geo_audit.step1.models import DiscoverySource

    runtime, _, _, _, _ = fixture(monkeypatch, tmp_path, [], entry=html(), max_sitemap_files=0)
    crawler = Crawler(runtime)
    url = runtime.handoff.contract.site.effective_url
    crawler.admit(
        url,
        DiscoverySource(kind="entry", source_id=runtime.store.entry_evidence_id, occurrence=0),
        entry=True,
    )
    page = crawler.page(
        crawler.candidates[url], url, runtime.store.entry_evidence_id, "html", complete_headers=True
    )

    def forbidden(*a, **k):
        raise AssertionError("DOM builder must not receive token bomb")

    monkeypatch.setattr(extraction, "BeautifulSoup", forbidden)
    result = extraction.extract_page(runtime, page, "<b></b>" * 60000, ())
    assert result.extraction_reason == "EXTRACTION_NODE_LIMIT" and not result.useful_candidate


def test_metadata_truncation_never_gives_noindex_absent_pass(monkeypatch, tmp_path):
    head = '<meta name="robots" content="index">' * 2000 + '<meta name="robots" content="noindex">'
    _, result, _, _, _ = execute_pipeline(monkeypatch, tmp_path, entry=html(head=head))
    assert "METADATA_TRUNCATED" in result.run.limitations
    assert any(
        c.kind == "indexing_directives" and c.status == "unknown" for c in result.checks.checks
    )


@pytest.mark.parametrize(
    "title,text,state",
    [
        ("Contact us", "Address: Berlin, Germany. Call +49 123 456 7890.", "html"),
        ("Page not found", "This page was not found.", "soft_404"),
        ("Sign in", "Please sign in to continue.", "access_barrier"),
        (
            "Privacy policy",
            "We provide this privacy policy for website visitors. " * 20,
            "service_content",
        ),
    ],
)
def test_short_supporting_and_nonbusiness_states(monkeypatch, tmp_path, title, text, state):
    _, result, _, _, _ = execute_pipeline(
        monkeypatch,
        tmp_path,
        entry=html(links='<a href="/ru/contacts">Contacts</a>'),
        scripts=[wire(html(text, title=title))],
    )
    page = result.crawl.pages[1]
    assert page.content_state == state
    assert not page.useful_candidate
    assert page.supporting_page == (title == "Contact us")
    if title == "Contact us":
        assert page.page_id in result.crawl.material.supporting_page_ids


def test_cancel_preserves_completed_ua_observations(monkeypatch, tmp_path):
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [wire(b"User-agent: *\nAllow: /"), wire(html())],
        entry=html(),
        max_sitemap_files=0,
        ua_probes_enabled=True,
    )
    original = runtime.fetch
    probe_count = 0

    async def fetch(url, policy, **kwargs):
        nonlocal probe_count
        if policy.purpose == "ua_probe":
            probe_count += 1
            if probe_count == 2:
                raise asyncio.CancelledError()
        return await original(url, policy, **kwargs)

    monkeypatch.setattr(runtime, "fetch", fetch)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_step1(runtime))
    saved = load_step1_result(runtime.store.io.root)
    assert saved.run.lifecycle == "cancelled"
    assert len([d for d in saved.checks.diagnostics if d.kind == "ua_probe" and d.url]) == 1
    assert saved.run.budget.total_attempts == len(connector.calls) == 3


def test_retry_after_blocks_subsequent_identity_probes(monkeypatch, tmp_path):
    _, result, _, _, connector = execute_pipeline(
        monkeypatch,
        tmp_path,
        entry=html(links='<a href="/ru/about">About</a>'),
        scripts=[wire(b"", status=429, headers=b"Retry-After: 999\r\n")],
        ua_probes_enabled=True,
    )
    assert result.run.status == "partial"
    assert result.run.budget.total_attempts == len(connector.calls) == 3
    assert any(
        d.kind == "ua_probe" and d.status == "unknown" and d.reason_code == "RETRY_DEFERRED"
        for d in result.checks.diagnostics
    )


def test_certificate_failure_survives_fallback_with_matching_evidence(monkeypatch, tmp_path):
    import ssl

    site, client, budget, clock, connector, _ = execute(
        monkeypatch,
        tmp_path / "source",
        [
            [ssl.SSLCertVerificationError(1, "controlled")],
            wire(html()),
            wire(b"User-agent: *\nAllow: /"),
        ],
    )
    runtime = Step1Runtime(
        prepare_step1(site, budget=budget, timing=clock.services()),
        source_directory=site._run_directory,
        output_dir=tmp_path / "out",
        client=client,
        timing=clock.services(),
        config=Step1Config(source_step0_config_sha256=site.config_sha256, max_sitemap_files=0),
    )
    result = asyncio.run(run_step1(runtime))
    checked = next(c for c in result.checks.checks if c.kind == "https_transport")
    assert checked.status == "fail" and checked.reason_code == "CERTIFICATE_FAILURE_OBSERVED"
    records = {e.evidence_id: e for e in result.evidence}
    assert all(records[i].representation == "attempt_observation" for i in checked.evidence_ids)
    failed = {r.evidence_id for r in site.evidence if r.attempt_id == site.attempts[0].attempt_id}
    assert any(records[i].original_evidence_id in failed for i in checked.evidence_ids)
    assert result.run.budget.total_attempts == len(connector.calls) == 3


def test_fallback_preserves_repeated_values_in_distinct_table_rows(monkeypatch, tmp_path):
    import trafilatura

    monkeypatch.setattr(trafilatura, "extract", lambda *a, **k: "Navigation")
    body = html().replace(
        b"</main>",
        b"<table><tr><td>A</td><td>$10</td></tr><tr><td>B</td><td>$10</td></tr></table></main>",
    )
    runtime, result, _, clock, _ = execute_pipeline(monkeypatch, tmp_path, entry=body)
    from geo_audit.step1.handoff import read_corpus

    _, texts = read_corpus(runtime.store.io.root, now=clock.utcnow())
    text = texts[result.crawl.pages[0].main_text.evidence_id]
    assert text.count("$10") == 2
