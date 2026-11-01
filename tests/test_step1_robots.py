"""Protocol vectors, evidence lines and absence of forbidden transport side effects."""

import asyncio
import gzip
import json

import httpcore
import pytest
from pydantic import ValidationError

from geo_audit import Step0Config
from geo_audit.step1 import load_step1_result
from geo_audit.step1.registry import RobotEntry, RobotRegistry, robot_registry
from geo_audit.step1.robots import PARSER_VERSION, ParsedRobots
from tests.fakes import wire
from tests.test_step1_runtime import fixture_runtime, snapshot


@pytest.mark.parametrize(
    "text,path,token,decision,groups,rules",
    [
        (
            "User-agent: *\nDisallow: /private\nAllow: /private/open\n",
            "/private",
            "GEOAudit",
            "disallowed",
            (1,),
            (2,),
        ),
        (
            "User-agent: *\nDisallow: /private\nAllow: /private/open\n",
            "/private/open",
            "GEOAudit",
            "allowed",
            (1,),
            (3,),
        ),
        (
            "User-agent: *\nDisallow: /\nUser-agent: geoaudit\nAllow: /\n",
            "/a",
            "GEOAUDIT",
            "allowed",
            (3,),
            (4,),
        ),
        (
            "User-agent: GEOAudit\nDisallow: /a\nUser-agent: GEOAUDIT\nDisallow: /b\n",
            "/b",
            "GEOAudit",
            "disallowed",
            (1, 3),
            (4,),
        ),
        (
            "User-agent: GEOAudit\nUser-agent: GPTBot\nDisallow: /a\n",
            "/a",
            "GPTBot",
            "disallowed",
            (1, 2),
            (3,),
        ),
        ("User-agent: *\nDisallow: /a\nAllow: /a\n", "/a", "GEOAudit", "allowed", (1,), (3,)),
        ("User-agent: *\nDisallow:\n", "/a", "GEOAudit", "allowed", (1,), ()),
        ("User-agent: *\nDisallow: /*.pdf$\n", "/a.pdf", "GEOAudit", "disallowed", (1,), (2,)),
        (
            "User-agent: *\nDisallow: /*.pdf$\n",
            "/a.pdf?download=1",
            "GEOAudit",
            "allowed",
            (1,),
            (),
        ),
        (
            "User-agent: *\nDisallow: /a*middle*end$\n",
            "/aXXmiddleYYend",
            "GEOAudit",
            "disallowed",
            (1,),
            (2,),
        ),
        (
            "User-agent: *\nDisallow: /путь\n",
            "/%D0%BF%D1%83%D1%82%D1%8C",
            "GEOAudit",
            "disallowed",
            (1,),
            (2,),
        ),
        ("User-agent: *\nDisallow: /%62%61%7A\n", "/baz", "GEOAudit", "disallowed", (1,), (2,)),
        (
            "User-agent: *\nDisallow: /a?city=msk$\n",
            "/a?city=msk",
            "GEOAudit",
            "disallowed",
            (1,),
            (2,),
        ),
        ("User-agent: *\nDisallow: /Case\n", "/case", "GEOAudit", "allowed", (1,), ()),
        (
            "Disallow: /\nUser-agent: *\nInvalid line\nDisallow: /a\n",
            "/a",
            "GEOAudit",
            "disallowed",
            (2,),
            (4,),
        ),
        ("User-agent: OtherBot\nDisallow: /\n", "/a", "GEOAudit", "allowed", (), ()),
        ("User-agent: *\nDisallow: /\n", "/robots.txt", "GEOAudit", "allowed", (1,), ()),
        (
            "User-agent: GEO\nDisallow: /\nUser-agent: GEOAudit\nAllow: /\n",
            "/a",
            "GEOAudit",
            "allowed",
            (3,),
            (4,),
        ),
    ],
)
def test_rfc_vectors_and_exact_lines(text, path, token, decision, groups, rules):
    parsed = ParsedRobots(text)
    result = parsed.evaluate("https://example.com" + path, token)
    assert (result.decision, result.group_lines, result.rule_lines) == (decision, groups, rules)
    assert parsed.parser.can_fetch("https://example.com" + path, token) == (decision == "allowed")
    for number in result.rule_lines:
        assert text.splitlines()[number - 1].lower().startswith(("allow:", "disallow:"))


def test_parser_disagreement_has_no_fabricated_decisive_line(monkeypatch):
    parsed = ParsedRobots("User-agent: *\nDisallow: /a\n")
    monkeypatch.setattr(parsed.parser, "can_fetch", lambda *_: True)
    result = parsed.evaluate("https://example.com/a", "GEOAudit")
    assert result.decision == "unknown" and result.reason == "ROBOTS_PARSER_MISMATCH"
    assert result.rule_lines == result.group_lines == ()


def test_crawl_delay_extensions_and_line_errors():
    parsed = ParsedRobots(
        "User-agent: GEOAudit\nCrawl-delay: 2.5\nRequest-rate: 1/5\n"
        "bad line\nUser-agent: GEOAudit\nCrawl-delay: NaN\nDisallow: /a\n"
    )
    result = parsed.evaluate("https://example.com/a", "GEOAudit")
    assert result.delay == 2.5 and result.rule_lines == (7,)
    assert "IGNORED_LINE:3" in parsed.warnings and "INVALID_CRAWL_DELAY:6" in parsed.warnings


def test_decisive_line_uses_parser_encoded_pattern_priority():
    parsed = ParsedRobots("User-agent: *\nAllow: /a?x=1\nAllow: /a*x=1$\n")
    result = parsed.evaluate("https://example.com/a?x=1", "GEOAudit")
    assert result.decision == "allowed" and result.rule_lines == (2,)


def test_implicit_index_alias_does_not_get_a_fabricated_direct_allow_line():
    parsed = ParsedRobots("User-agent: *\nAllow: /\nAllow: /index.html\n")
    result = parsed.evaluate("https://example.com/", "GEOAudit")
    assert result.decision == "unknown" and result.rule_lines == ()


@pytest.mark.parametrize(
    "status,body,state,decision,attempts",
    [
        (200, b"", "empty", "allowed", 1),
        (200, b"# comments only\n", "empty", "allowed", 1),
        (204, b"", "empty", "allowed", 1),
        (404, b"missing", "absent", "allowed", 1),
        (410, b"gone", "absent", "allowed", 1),
        (401, b"unauthorized", "access_denied", "unknown", 1),
        (403, b"denied", "access_denied", "unknown", 1),
        (429, b"busy", "rate_limited", "unknown", 3),
        (503, b"unavailable", "unreachable", "unknown", 3),
        (
            200,
            b"<html><title>Verify you are human</title>Allow: /</html>",
            "invalid_response",
            "unknown",
            1,
        ),
        (206, b"User-agent: *\nAllow: /\n", "incomplete", "unknown", 1),
        (200, b"not a robots document", "invalid_response", "unknown", 1),
        (200, b"User-agent: *\nDisallow: /a\n\xff", "invalid_response", "unknown", 1),
    ],
)
def test_complete_status_table_and_cached_unknown(
    monkeypatch, tmp_path, status, body, state, decision, attempts
):
    runtime, site, clock, connector = fixture_runtime(
        monkeypatch,
        tmp_path,
        [
            wire(body, status=status, headers=b"Content-Type: text/plain\r\n")
            for _ in range(attempts)
        ],
    )
    before = snapshot(site._run_directory)

    async def work():
        async with runtime:
            first = await runtime.robots.evaluate("https://example.com/a", "GEOAudit")
            assert first.decision == decision
            assert runtime.robots.observations[0].state == state
            assert await runtime.robots.evaluate("https://example.com/a", "GEOAudit") is first
            assert (
                await runtime.robots.get("https://example.com")
                is runtime.robots.cache["https://example.com"]
            )

    asyncio.run(work())
    result = load_step1_result(runtime.store.io.root)
    assert result.run.budget.total_attempts == len(connector.calls) == 1 + attempts
    assert result.crawl.robots_decisions[0].parser_version == PARSER_VERSION
    assert snapshot(site._run_directory) == before
    if decision == "unknown":
        assert result.checks.checks[0].status == "unknown"
        assert result.crawl.robots_decisions[0].reason_code != "ROBOTS_FORBIDS"


@pytest.mark.parametrize(
    "kind", ["timeout", "partial", "oversize", "gzip_bomb", "redirect_private", "redirect_loop"]
)
def test_failure_resources_never_authorize_html(monkeypatch, tmp_path, kind):
    if kind == "timeout":
        scripts = [[httpcore.ReadTimeout("controlled timeout")] for _ in range(3)]
    elif kind == "partial":
        scripts = [
            [b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nUser-agent: *\n"] for _ in range(3)
        ]
    elif kind in {"oversize", "gzip_bomb"}:
        body = b"User-agent: *\nAllow: /\n" + b"#" * (512 * 1024)
        headers = b"Content-Type: text/plain\r\n"
        if kind == "gzip_bomb":
            body, headers = gzip.compress(body), headers + b"Content-Encoding: gzip\r\n"
        scripts = [wire(body, headers=headers)]
    elif kind == "redirect_private":
        scripts = [wire(status=302, headers=b"Location: http://127.0.0.1/robots\r\n")]
    else:
        scripts = [wire(status=302, headers=b"Location: /robots.txt\r\n")]
    runtime, site, clock, connector = fixture_runtime(monkeypatch, tmp_path, scripts)

    async def work():
        async with runtime:
            trace = await runtime.fetch_html("https://example.com/a")
            assert trace.policy_denial.decision == "unknown" and trace.attempts == ()
            assert runtime.robots.observations[0].state in {"unreachable", "incomplete"}

    asyncio.run(work())
    assert len(connector.calls) == 1 + len(scripts)
    assert all(stream.closed for stream in connector.streams)
    assert all(b"GET /a " not in b"".join(s.writes) for s in connector.streams)
    if kind in {"oversize", "gzip_bomb"}:
        observation = runtime.robots.observations[0]
        assert observation.truncated
        assert all(d.reason_code == "ROBOTS_TRUNCATED" for d in runtime.robots.decisions.values())


def test_html_redirect_checks_new_origin_and_url_before_get(monkeypatch, tmp_path):
    robots = b"User-agent: *\nDisallow: /ru/blocked\nCrawl-delay: 2\n"
    runtime, site, clock, connector = fixture_runtime(
        monkeypatch,
        tmp_path,
        [
            wire(robots, headers=b"Content-Type: text/plain\r\n"),
            wire(status=302, headers=b"Location: http://example.com/ru/blocked\r\n"),
            wire(robots, headers=b"Content-Type: text/plain\r\n"),
        ],
        Step0Config(path_scope=("/ru/",)),
    )

    async def work():
        async with runtime:
            trace = await runtime.fetch_html("https://example.com/ru/open")
            assert trace.policy_denial.decision == "disallowed"
            assert len(trace.attempts) == 1
            assert (
                await runtime.robots.evaluate("http://example.com/ru/blocked", "GEOAudit")
            ).decisive_rule_lines == (2,)

    asyncio.run(work())
    assert len(connector.calls) == 4
    assert set(runtime.robots.cache) == {"http://example.com", "https://example.com"}
    assert all(b"GET /ru/blocked " not in b"".join(s.writes) for s in connector.streams)
    assert 2.0 in clock.delays


def test_external_robots_redirect_binds_initial_origin_without_html_scope_expansion(
    monkeypatch, tmp_path
):
    runtime, site, clock, connector = fixture_runtime(
        monkeypatch,
        tmp_path,
        [
            wire(status=302, headers=b"Location: https://cdn.test/policy\r\n"),
            wire(
                b"User-agent: *\nDisallow: /ru/private\n", headers=b"Content-Type: text/plain\r\n"
            ),
        ],
        Step0Config(path_scope=("/ru/",)),
    )

    async def work():
        async with runtime:
            trace = await runtime.fetch_html("https://example.com/ru/private")
            assert trace.policy_denial.decision == "disallowed"
            decision = await runtime.robots.evaluate("https://example.com/ru/private", "GEOAudit")
            assert decision.origin == "https://example.com" and decision.decisive_rule_lines == (2,)
            observation = runtime.robots.observations[0]
            assert (
                observation.origin == "https://example.com"
                and observation.final_url == "https://cdn.test/policy"
            )

    asyncio.run(work())
    assert len(connector.calls) == 3 and runtime.scope.allowed_hosts == frozenset({"example.com"})
    saved = load_step1_result(runtime.store.io.root)
    decision = saved.crawl.robots_decisions[0]
    record = next(e for e in saved.evidence if e.evidence_id == decision.robots_evidence_id)
    assert (runtime.store.io.root / record.file.path).read_text(encoding="utf-8").splitlines()[
        1
    ] == "Disallow: /ru/private"


@pytest.mark.parametrize("hops", [5, 6])
def test_five_safe_redirects_are_a_shared_budget_bound(monkeypatch, tmp_path, hops):
    scripts = [
        wire(status=302, headers=f"Location: https://cdn{n}.test/policy\r\n".encode())
        for n in range(hops)
    ]
    if hops == 5:
        scripts.append(wire(b"User-agent: *\n", headers=b"Content-Type: text/plain\r\n"))
    runtime, site, clock, connector = fixture_runtime(monkeypatch, tmp_path, scripts)

    async def work():
        async with runtime:
            decision = await runtime.robots.evaluate("https://example.com/ru/", "GEOAudit")
            assert decision.decision == ("allowed" if hops == 5 else "unknown")

    asyncio.run(work())
    assert len(connector.calls) == 7 and len(runtime.robots.cache) == 1


def test_registry_roles_null_ua_snapshot_and_no_probes(monkeypatch, tmp_path):
    registry = robot_registry()
    assert len(registry.entries) == 8
    assert RobotRegistry.model_validate_json(registry.model_dump_json()) == registry
    extended = next(e for e in registry.entries if e.product_token == "Google-Extended")
    assert extended.http_user_agent is None
    with pytest.raises(ValidationError):
        RobotEntry.model_validate_json(
            extended.model_copy(update={"http_user_agent": "fake"}).model_dump_json()
        )
    text = b"User-agent: Google-Extended\nDisallow: /\nUser-agent: *\nAllow: /\n"
    runtime, site, clock, connector = fixture_runtime(monkeypatch, tmp_path, [wire(text)])

    async def work():
        async with runtime:
            decisions = await runtime.robots.evaluate_all("https://example.com/ru/")
            assert (
                len(decisions) == 9
                and next(d for d in decisions if d.product_token == "Google-Extended").decision
                == "disallowed"
            )

    asyncio.run(work())
    saved = load_step1_result(runtime.store.io.root)
    assert saved.run.registry_version == registry.registry_version
    record = next(e for e in saved.evidence if e.representation == "registry_snapshot")
    assert json.loads(
        (runtime.store.io.root / record.file.path).read_bytes()
    ) == registry.model_dump(mode="json")
    assert len(connector.calls) == 2
    assert all(b"User-Agent: GEOAudit/0.1\r\n" in b"".join(s.writes) for s in connector.streams)
