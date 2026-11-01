"""Bounded sitemap discovery: hostile XML, gzip, provenance and transport journals."""

import asyncio
import gzip
import socket

import pytest
from pydantic import ValidationError

from geo_audit import Step0Config
from geo_audit.handoff import prepare_step1
from geo_audit.step1 import Step1Config, Step1Runtime, load_step1_result
from geo_audit.step1.models import SitemapEntry
from geo_audit.step1.sitemap import parse_sitemap, unpack_sitemap
from tests.fakes import wire
from tests.test_step0_end_to_end import execute
from tests.test_step1_runtime import snapshot

NS = ' xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
ENTRY = b'<!doctype html><html><title>Company</title><a href="about#x">About</a></html>'


def xml(urls, *, index=False, extra=""):
    root, item = ("sitemapindex", "sitemap") if index else ("urlset", "url")
    return (
        f"<{root}{NS}>"
        + "".join(f"<{item}><loc>{url}</loc>{extra}</{item}>" for url in urls)
        + f"</{root}>"
    ).encode()


def cfg(**kwargs):
    return Step1Config(source_step0_config_sha256="0" * 64, **kwargs)


def fixture(monkeypatch, tmp_path, scripts, *, entry=ENTRY, **overrides):
    site, client, budget, clock, connector, resolver = execute(
        monkeypatch,
        tmp_path / "source",
        [wire(entry), *scripts],
        config=Step0Config(path_scope=("/ru/",)),
    )
    runtime = Step1Runtime(
        prepare_step1(site, budget=budget, timing=clock.services()),
        source_directory=site._run_directory,
        output_dir=tmp_path / "out",
        client=client,
        timing=clock.services(),
        config=Step1Config(source_step0_config_sha256=site.config_sha256, **overrides),
    )
    return runtime, site, clock, connector, resolver


def run_discovery(runtime):
    async def work():
        async with runtime:
            return await runtime.discover_urls()

    return asyncio.run(work())


def requests(connector):
    return [b"".join(stream.writes).split(b"\r\n", 1)[0] for stream in connector.streams]


def test_namespace_loc_publisher_metadata_and_exact_ordinals(monkeypatch, tmp_path):
    body = xml(
        ["relative", "https://example.com/ru/about", "https://example.com/ru/?b=2&amp;a=1"],
        extra="<lastmod>publisher-invalid-date</lastmod><priority>99</priority>",
    )
    runtime, site, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [wire(b"User-agent: *\nAllow: /\nSitemap: https://example.com/map.xml"), wire(body)],
    )
    before = snapshot(site._run_directory)
    result = run_discovery(runtime)
    assert [u.url for u in result.urls] == [
        "https://example.com/ru/?secret=local",
        "https://example.com/ru/about",
        "https://example.com/ru/?b=2&a=1",
    ]
    assert [s.kind for s in result.urls[1].sources] == ["html_link", "sitemap"]
    assert result.urls[1].sources[1].occurrence == 1
    assert result.urls[2].sources[0].occurrence == 2
    saved = load_step1_result(runtime.store.io.root)
    observed = saved.crawl.sitemaps[0]
    assert observed.entries[0].url is None and not observed.entries[0].inferred_url
    assert observed.publisher_metadata[0] == ("0/lastmod", "publisher-invalid-date")
    assert len(connector.calls) == saved.run.budget.total_attempts == 3
    assert snapshot(site._run_directory) == before


@pytest.mark.parametrize("robots_status", [200, 404, 503])
def test_limited_standard_paths_and_unknown_keeps_html_links(monkeypatch, tmp_path, robots_status):
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [
            wire(b"User-agent: *\nAllow: /", status=robots_status),
            wire(b"", status=503 if robots_status == 503 else 404),
            wire(b"", status=503 if robots_status == 503 else 410),
        ],
    )
    result = run_discovery(runtime)
    paths = requests(connector)
    assert any(u.url.endswith("/ru/about") for u in result.urls)
    if robots_status == 503:
        assert len(connector.calls) == 4  # three failed robots attempts, no sitemap
        assert runtime.discovery.observations == []
        assert dict(result.problem_counts)["ROBOTS_POLICY_UNKNOWN"] == 1
        assert not any(b"GET /ru/about " in p for p in paths)
    else:
        assert [o.state for o in runtime.discovery.observations] == ["absent", "absent"]
        assert len(connector.calls) == 4
        assert paths[-2:] == [b"GET /sitemap.xml HTTP/1.1", b"GET /sitemap_index.xml HTTP/1.1"]


def test_successful_standard_map_stops_fallback(monkeypatch, tmp_path):
    runtime, _, _, connector, _ = fixture(
        monkeypatch, tmp_path, [wire(b"", status=404), wire(xml(["https://example.com/ru/about"]))]
    )
    result = run_discovery(runtime)
    assert len(result.sitemap_ids) == 1
    assert len(connector.calls) == 3


def test_cycles_duplicates_depth_and_global_file_limit(monkeypatch, tmp_path):
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [
            wire(b"User-agent: *\nAllow: /\nSitemap: https://example.com/a.xml"),
            wire(xml(["https://example.com/a.xml", "https://example.com/b.xml"], index=True)),
            wire(xml(["https://example.com/c.xml"], index=True)),
            wire(xml(["https://example.com/d.xml"], index=True)),
            wire(b"", status=404),
            wire(b"", status=404),
        ],
    )
    result = run_discovery(runtime)
    assert len(result.sitemap_ids) == 5  # index files count too; fallback is bounded
    assert result.sitemap_truncated
    assert dict(result.problem_counts)["SITEMAP_DUPLICATE"] == 1
    assert dict(result.problem_counts)["SITEMAP_DEPTH_LIMIT"] == 1
    assert not any(b"GET /d.xml " in p for p in requests(connector))
    assert len(connector.calls) == 7
    previous = len(connector.calls)
    assert asyncio.run(runtime.discover_urls()) is result
    assert len(connector.calls) == previous


def test_all_entries_share_limit_including_index_and_invalid(monkeypatch, tmp_path):
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [
            wire(b"User-agent: *\nAllow: /\nSitemap: https://example.com/a.xml"),
            wire(xml(["https://example.com/b.xml"], index=True)),
            wire(xml(["relative", "https://example.com/ru/ok", "https://example.com/ru/excess"])),
        ],
        max_sitemap_urls=3,
    )
    result = run_discovery(runtime)
    assert sum(o.parsed_entries for o in runtime.discovery.observations) == 3
    assert result.sitemap_truncated
    assert any(u.url.endswith("/ru/ok") for u in result.urls)
    assert not any(u.url.endswith("excess") for u in result.urls)
    assert len(connector.calls) == 4


@pytest.mark.parametrize("allowed", [False, True])
def test_cdn_requires_explicit_service_scope_and_own_robots(monkeypatch, tmp_path, allowed):
    scripts = [wire(b"User-agent: *\nAllow: /\nSitemap: https://cdn.example.net/map.xml")]
    if allowed:
        scripts.extend(
            [wire(b"User-agent: *\nAllow: /"), wire(xml(["https://example.com/ru/about"]))]
        )
    else:
        scripts.extend([wire(b"", status=404), wire(b"", status=404)])
    runtime, _, _, connector, resolver = fixture(
        monkeypatch,
        tmp_path,
        scripts,
        allowed_sitemap_hosts=("cdn.example.net",) if allowed else (),
    )
    run_discovery(runtime)
    assert "cdn.example.net" not in runtime.scope.allowed_hosts
    cdn_calls = [call for call in resolver.calls if call[0] == "cdn.example.net"]
    assert len(cdn_calls) == (2 if allowed else 0)
    if allowed:
        assert requests(connector)[-2:] == [b"GET /robots.txt HTTP/1.1", b"GET /map.xml HTTP/1.1"]


def test_map_is_not_html_permission_and_denied_standard_map_has_no_get(monkeypatch, tmp_path):
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [
            wire(
                b"User-agent: *\nDisallow: /ru/private\nDisallow: /sitemap.xml\n"
                b"Sitemap: https://example.com/map.xml"
            ),
            wire(xml(["https://example.com/ru/private"])),
        ],
    )

    async def work():
        async with runtime:
            result = await runtime.discover_urls()
            assert any(u.url.endswith("private") for u in result.urls)
            trace = await runtime.fetch_html("https://example.com/ru/private")
            assert trace.policy_denial.decision == "disallowed"

    asyncio.run(work())
    assert len(connector.calls) == 3
    assert not any(b"GET /ru/private " in p for p in requests(connector))


@pytest.mark.parametrize(
    "body",
    [
        b'<!DOCTYPE urlset SYSTEM "http://127.0.0.1/secret"><urlset/>',
        b'<!DOCTYPE urlset [<!ENTITY x SYSTEM "file:///C:/secret">]><urlset>&x;</urlset>',
        b'<!DOCTYPE urlset [<!ENTITY a "lol"><!ENTITY b "&a;&a;">]><urlset>&b;</urlset>',
        b"<urlset><url><loc>https://example.com/ru/a</loc></url>",
        b'<urlset xmlns="https://attacker.invalid/not-sitemap"/>',
        b"<html/>",
    ],
)
def test_hostile_xml_never_opens_network(monkeypatch, body):
    def forbidden(*args, **kwargs):
        pytest.fail("XML parser attempted network")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    parsed = parse_sitemap(body, cfg(), 5000, lambda: None)
    assert parsed.state == "invalid"


def test_missing_duplicate_nested_loc_and_namespace_extensions():
    body = (
        f'<urlset{NS} xmlns:news="https://other.invalid">'
        "<url><loc>/relative</loc></url><url/>"
        "<url><loc>https://example.com/a</loc><loc>https://example.com/b</loc></url>"
        "<url><loc>https://example.com/<b>hidden</b></loc></url>"
        "<url><news:loc>https://example.com/wrong</news:loc></url>"
        "<url><loc>https://example.com/valid</loc></url></urlset>"
    ).encode()
    result = parse_sitemap(body, cfg(), 5000, lambda: None)
    assert result.state == "parsed"
    assert [e.state for e in result.entries] == ["skipped"] * 5 + ["accepted"]
    assert all(not e.inferred_url for e in result.entries)


@pytest.mark.parametrize("limit", ["depth", "nodes"])
def test_xml_work_and_time_limits(monkeypatch, limit):
    import geo_audit.step1.sitemap as module

    override = {"max_xml_depth": 3} if limit == "depth" else {}
    if limit == "nodes":
        monkeypatch.setattr(module, "MAX_XML_NODES", 3)
    result = parse_sitemap(
        b"<urlset><url><loc><deep/></loc></url></urlset>", cfg(**override), 5000, lambda: None
    )
    assert result.state == "incomplete" and result.truncated

    def expired():
        raise RuntimeError("expired operation")

    with pytest.raises(RuntimeError, match="expired operation"):
        parse_sitemap(xml(["https://example.com/a"]), cfg(), 5000, expired)


@pytest.mark.parametrize("case", ["bomb", "truncated", "concatenated"])
def test_gzip_file_caps_and_incomplete(case):
    data = gzip.compress(b"x" * (10000 if case == "bomb" else 100))
    if case == "truncated":
        data = data[:-5]
    elif case == "concatenated":
        data += gzip.compress(b"second member")
    output, complete, reason = unpack_sitemap(data, cfg(max_sitemap_bytes=1024), lambda: None)
    assert len(output) <= 1024
    assert not complete and reason.startswith("SITEMAP_GZIP_")


def test_http_gzip_and_gzip_file_are_separate_evidence_layers(monkeypatch, tmp_path):
    body = xml(["https://example.com/ru/about"])
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [
            wire(b"User-agent: *\nAllow: /\nSitemap: https://example.com/map.xml.gz"),
            wire(
                gzip.compress(gzip.compress(body)),
                headers=b"Content-Type: application/gzip\r\nContent-Encoding: gzip\r\n",
            ),
        ],
    )
    result = run_discovery(runtime)
    assert result.unpacked_bytes == len(body)
    saved = load_step1_result(runtime.store.io.root)
    derived = next(e for e in saved.evidence if e.representation == "sitemap_xml")
    parent = next(e for e in saved.evidence if e.evidence_id == derived.source_evidence_id)
    assert parent.representation == "entity_bytes"
    assert derived.transform_version == "gzip-file-decoding-1"
    assert len(connector.calls) == 3


def test_retry_after_does_not_trigger_fallback_to_evade_wait(monkeypatch, tmp_path):
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [
            wire(b"User-agent: *\nAllow: /\nSitemap: https://example.com/map.xml"),
            wire(b"", status=429, headers=b"Retry-After: 999\r\n"),
        ],
    )
    result = run_discovery(runtime)
    assert len(connector.calls) == 3
    assert result.sitemap_truncated
    assert runtime.discovery.observations[0].reason_code == "RETRY_DEFERRED"


def test_bounded_link_and_problem_examples_keep_late_base(monkeypatch, tmp_path):
    entry = (
        b'<html><title>A</title><a href="about">A</a><a href="more">B</a>'
        b'<a href="overflow">C</a><base href="/ru/"></html>'
    )
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [],
        entry=entry,
        max_candidates=2,
        max_skip_examples=0,
        max_sitemap_files=0,
    )
    result = run_discovery(runtime)
    assert result.urls[1].url == "https://example.com/ru/about"
    assert result.discovery_truncated and result.examples_truncated
    assert result.problem_examples == ()
    assert len(connector.calls) == 1


@pytest.mark.parametrize("url,state", [(None, "accepted"), ("https://example.com/a", "skipped")])
def test_sitemap_entry_rejects_contradictory_state(url, state):
    with pytest.raises(ValidationError):
        SitemapEntry(
            ordinal=0,
            raw_loc="https://example.com/a",
            url=url,
            state=state,
            publisher_metadata=(),
            issues=(),
        )


def test_robots_denies_both_standard_maps_before_get(monkeypatch, tmp_path):
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [wire(b"User-agent: *\nDisallow: /sitemap.xml\nDisallow: /sitemap_index.xml")],
    )
    result = run_discovery(runtime)
    assert len(connector.calls) == 2
    assert len(result.sitemap_ids) == 2
    assert all(o.state == "skipped" for o in runtime.discovery.observations)
    assert any(u.url.endswith("/ru/about") for u in result.urls)


def test_map_redirect_checks_cdn_robots_before_destination(monkeypatch, tmp_path):
    runtime, _, _, connector, resolver = fixture(
        monkeypatch,
        tmp_path,
        [
            wire(b"User-agent: *\nAllow: /\nSitemap: https://example.com/map.xml"),
            wire(b"", status=302, headers=b"Location: https://cdn.example.net/blocked.xml\r\n"),
            wire(b"User-agent: *\nDisallow: /blocked.xml"),
        ],
        allowed_sitemap_hosts=("cdn.example.net",),
        max_sitemap_files=1,
    )
    run_discovery(runtime)
    assert len(connector.calls) == runtime.result.run.budget.total_attempts == 4
    assert resolver.calls[-1][0] == "cdn.example.net"
    assert not any(b"GET /blocked.xml " in p for p in requests(connector))
    assert runtime.discovery.observations[0].state == "skipped"


def test_default_5000_entry_boundary_is_shared_work_cap():
    result = parse_sitemap(
        xml([f"https://example.com/{n}" for n in range(5001)]), cfg(), 5000, lambda: None
    )
    assert len(result.entries) == 5000
    assert result.state == "incomplete" and result.reason == "SITEMAP_URL_LIMIT"


def test_legacy_step1_config_fingerprint_remains_readable():
    # Golden config/hash from the valid synthetic artifact at 9f231de.
    previous = Step1Config(
        source_step0_config_sha256="cc5a909336e4d69681426385223c5409ea133fe46c5bfe7bc4a465146d394fe8"
    )
    assert previous.fingerprint() == (
        "e55f767087bd1542687d9e2b99381fae3b63d9dd3e3acf4d60c7263dc24aff25"
    )


def test_malformed_map_entries_are_not_admitted_and_links_survive(monkeypatch, tmp_path):
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [
            wire(b"User-agent: *\nAllow: /\nSitemap: https://example.com/map.xml"),
            wire(b"<urlset><url><loc>https://example.com/ru/unsafe-hint</loc></url>"),
        ],
        max_sitemap_files=1,
    )
    result = run_discovery(runtime)
    assert runtime.discovery.observations[0].state == "invalid"
    assert not any("unsafe-hint" in u.url for u in result.urls)
    assert any(u.url.endswith("about") for u in result.urls)
    assert len(connector.calls) == 3
