"""Deterministic slots, exact URL semantics and requests forbidden before transport."""

import asyncio

import pytest

from geo_audit.step1 import load_step1_result
from geo_audit.step1.crawler import role_for
from tests.fakes import wire
from tests.test_step1_sitemap import fixture, requests


def crawl(runtime):
    async def work():
        async with runtime:
            await runtime.crawl_pages()

    asyncio.run(work())
    return load_step1_result(runtime.store.io.root)


def test_priority_query_order_and_bounded_slots(monkeypatch, tmp_path):
    entry = (
        b'<html><title>Company</title><a href="/ru/faq">FAQ</a>'
        b'<a href="/ru/services?b=2&amp;a=1#x">Services</a>'
        b'<a href="/ru/about">About</a><a href="/ru/prices">Prices</a></html>'
    )
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [wire(b"User-agent: *\nAllow: /"), wire(), wire(status=404), wire(), wire()],
        entry=entry,
        max_sitemap_files=0,
        max_selected=4,
    )
    result = crawl(runtime)
    assert requests(connector)[2:] == [
        b"GET /ru/about HTTP/1.1",
        b"GET /ru/services?b=2&a=1 HTTP/1.1",
        b"GET /ru/prices HTTP/1.1",
    ]
    assert result.crawl.coverage.selected_candidates == 4
    assert result.crawl.coverage.failed == 1
    assert result.run.budget.total_attempts == len(connector.calls) == 5
    assert result.crawl.candidates[1].state == "not_selected_limit"


def test_parameter_traps_exact_segments_and_external_branch(monkeypatch, tmp_path):
    entry = (
        b'<html><title>Company</title><a href="/ru/?n=1">1</a>'
        b'<a href="/ru/?n=2">2</a><a href="/ru/?n=3">3</a>'
        b'<a href="/ru/cart">Cart</a><a href="/ru/accounting">Accounting</a>'
        b'<a href="/ru/other?q=foo">Search</a><a href="https://outside.example/a">Out</a></html>'
    )
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [wire(b"User-agent: *\nAllow: /"), wire(), wire()],
        entry=entry,
        max_sitemap_files=0,
    )
    result = crawl(runtime)
    assert result.crawl.coverage.selected_candidates == 3  # entry query consumes a variant
    assert sum(c.state == "skipped_parameter_limit" for c in result.crawl.candidates) == 2
    assert sum(c.state == "skipped_excluded" for c in result.crawl.candidates) == 2
    assert result.crawl.pending_external_urls == ("https://outside.example/a",)
    assert requests(connector)[-1] == b"GET /ru/accounting HTTP/1.1"
    assert len(connector.calls) == result.run.budget.total_attempts == 4


def test_redirect_alias_keeps_slot_and_redirect_exclusion_prevents_get(monkeypatch, tmp_path):
    entry = (
        b'<html><title>Company</title><a href="/ru/about">Alias</a><a href="/ru/go">Go</a></html>'
    )
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [
            wire(b"User-agent: *\nAllow: /"),
            wire(b"", status=302, headers=b"Location: /ru/?secret=local\r\n"),
            wire(b"", status=302, headers=b"Location: /ru/logout\r\n"),
        ],
        entry=entry,
        max_sitemap_files=0,
    )
    result = crawl(runtime)
    assert result.crawl.coverage.selected_candidates == 3
    assert result.crawl.coverage.unique_html_pages == 1
    assert result.crawl.coverage.fetch_successes == 1
    assert len(result.crawl.pages) == 1
    assert result.crawl.candidates[1].alias_of == "candidate-00001"
    assert result.crawl.candidates[2].state == "skipped_excluded"
    assert not any(b"GET /ru/logout " in r for r in requests(connector))
    assert len(connector.calls) == result.run.budget.total_attempts == 4


@pytest.mark.parametrize(
    "url,role",
    [
        ("https://example.com/aboutness", "other"),
        ("https://example.com/ru/about", "about"),
        ("https://xn--e1afmkfd.xn--p1ai:8443/products", "services"),
    ],
)
def test_role_is_exact_versioned_heuristic(url, role):
    assert role_for(url)[0] == role


def test_cancellation_has_no_queued_or_unexplained_selected(monkeypatch, tmp_path):
    entry = b'<html><title>Company</title><a href="/ru/about">About</a></html>'
    runtime, _, _, connector, _ = fixture(
        monkeypatch, tmp_path, [wire(b"User-agent: *\nAllow: /")], entry=entry, max_sitemap_files=0
    )
    original = runtime.fetch_html

    async def cancelled(url):
        raise asyncio.CancelledError()

    runtime.fetch_html = cancelled
    with pytest.raises(asyncio.CancelledError):
        crawl(runtime)
    runtime.fetch_html = original
    result = load_step1_result(runtime.store.io.root)
    assert result.run.lifecycle == "cancelled"
    assert result.crawl.candidates[1].selected
    assert result.crawl.candidates[1].state == "cancelled"
    assert len(connector.calls) == 2


def test_finalizing_fetch_keeps_self_link_provenance(monkeypatch, tmp_path):
    entry = b'<html><title>Company</title><a href="/ru/about">About</a></html>'
    runtime, _, _, _, _ = fixture(
        monkeypatch,
        tmp_path,
        [wire(b"User-agent: *\nAllow: /"), wire(entry)],
        entry=entry,
        max_sitemap_files=0,
    )
    result = crawl(runtime)
    candidate = result.crawl.candidates[1]
    assert {s.source_id for s in candidate.sources if s.kind == "html_link"} == {
        "page-00001",
        "page-00002",
    }


def test_action_query_exclusions_have_exact_value_boundaries(monkeypatch, tmp_path):
    entry = (
        b'<html><title>Company</title><a href="/ru/page?action=logout">Logout</a>'
        b'<a href="/ru/page?action=accounting">Accounting</a></html>'
    )
    runtime, _, _, connector, _ = fixture(
        monkeypatch,
        tmp_path,
        [wire(b"User-agent: *\nAllow: /"), wire()],
        entry=entry,
        max_sitemap_files=0,
    )
    result = crawl(runtime)
    assert result.crawl.candidates[1].state == "skipped_excluded"
    assert result.crawl.candidates[2].state == "fetched"
    assert requests(connector)[-1] == b"GET /ru/page?action=accounting HTTP/1.1"
