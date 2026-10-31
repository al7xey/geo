import socket

import httpx
import pytest

from geo_audit import GeoAuditError
from geo_audit.policy import choose_next_action, classify_result
from geo_audit.psl import load_bundled_psl
from geo_audit.scope import build_scope, decide_redirect, scope_reason
from geo_audit.urls import normalize_host, normalize_input, resolve_location


@pytest.fixture(scope="module")
def psl():
    return load_bundled_psl()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("example.com:8080/Services?a=1&a=2#x", "https://example.com:8080/Services?a=1&a=2"),
        (
            "https://ПРИМЕР.РФ/Тест?x=%2f+%20&x=&empty#x",
            "https://xn--e1afmkfd.xn--p1ai/%D0%A2%D0%B5%D1%81%D1%82?x=%2f+%20&x=&empty",
        ),
        ("https://faß.de/%41%7e?x=a;b?c=d", "https://xn--fa-hia.de/%41%7e?x=a;b?c=d"),
        ("https://example.com//A///B?", "https://example.com//A///B?"),
        ("https://EXAMPLE.com./", "https://example.com/"),
        ("https://example.com/a%2Eb?q=%252f", "https://example.com/a%2Eb?q=%252f"),
    ],
)
def test_lossless_normalization_invariants(raw, expected, monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *_: pytest.fail("pure normalization used DNS")
    )
    result = normalize_input(raw)
    assert result.input_url == raw
    assert result.url == expected
    assert normalize_input(result.url).url == result.url
    assert (
        httpx.Request("GET", result.url).url.raw_path
        == expected.split(result.origin, 1)[1].encode()
    )
    assert normalize_input(result.url).host_ascii == result.host_ascii


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com/%2e%2e/x",
        "https://example.com/a%2Fb",
        "https://example.com/%252e%252e/",
        "https://example.com/a%5cb",
        "https://example.com/%00",
        "https://example.com/%3fadmin",
        "https://example.com/a\u0085b",
        "https://ex\u200bample.com/",
        "https://%65xample.com/",
        "https://example.com:１２/",
        "https://[v1.foo]/",
        "https://[::1%25zone]/",
        "https://127.1/",
        "https://0177.0.0.1/",
        "https://０１７７.０.０.１/",
        "https://0x7f.0.0.1/",
        "https://1.2.3.0xff/",
        "https://example.123/",
        "https://foo..com/",
        "https://-foo.com/",
        "https://example.com../",
        "mailto:a@example.com",
        "https:example.com",
        "https:///example.com",
        "https://[::1]junk/",
    ],
)
def test_ambiguous_inputs_are_rejected_without_rewriting(raw):
    with pytest.raises(GeoAuditError):
        normalize_input(raw)


@pytest.mark.parametrize(
    "host", ["example.com:443", "127.1", "[::1]", "0x7f000001", "user@host", "a/b"]
)
def test_host_is_not_an_origin(host):
    with pytest.raises(ValueError):
        normalize_host(host)


@pytest.mark.parametrize(
    "location,expected",
    [
        ("../C?x=1&x=2#frag", "https://example.com/C?x=1&x=2"),
        ("//www.example.com/X", "https://www.example.com/X"),
        ("?", "https://example.com/A/B?"),
        ("#x", "https://example.com/A/B?old=1"),
        ("./C", "https://example.com/A/C"),
        ("../../../../X", "https://example.com/X"),
        ("/A//B/../C", "https://example.com/A//C"),
        ("C?key=%252f&key=%2F", "https://example.com/A/C?key=%252f&key=%2F"),
    ],
)
def test_relative_location(location, expected):
    assert resolve_location("https://example.com/A/B?old=1", location).url == expected


@pytest.mark.parametrize(
    "location",
    ["", "///evil.com", "//u:p@evil.com", "ftp://example.com", "%2e%2e/X", "a\\b", "x\n"],
)
def test_invalid_location(location):
    with pytest.raises(GeoAuditError) as error:
        resolve_location("https://example.com/A/", location)
    assert error.value.detail.code == "REDIRECT_INVALID_LOCATION"


@pytest.mark.parametrize(
    "source,target,allowed",
    [
        ("example.com", "www.example.com", True),
        ("www.example.co.uk", "example.co.uk", True),
        ("пример.рф", "www.пример.рф", True),
        ("example.com", "shop.example.com", False),
        ("github.io", "www.github.io", False),
        ("a.github.io", "www.a.github.io", True),
        ("a.github.io", "b.github.io", False),
        ("blogspot.com", "www.blogspot.com", False),
        ("a.blogspot.com", "www.a.blogspot.com", True),
        ("co.uk", "www.co.uk", False),
        ("foo.ck", "www.foo.ck", False),
        ("www.ck", "ck", False),
        ("www.ck", "www.www.ck", True),
        ("city.kawasaki.jp", "www.city.kawasaki.jp", True),
        ("foo.kawasaki.jp", "www.foo.kawasaki.jp", False),
        ("example.unknownsuffix", "www.example.unknownsuffix", False),
        ("127.0.0.1", "www.127.0.0.1", False),
    ],
)
def test_www_uses_real_psl_private_wildcard_exception(psl, source, target, allowed):
    assert psl.is_www_pair(source, target) is allowed


@pytest.mark.parametrize(
    "path,allowed", [("/ru", True), ("/ru/Services", True), ("/rub", False), ("/%72u/A", True)]
)
def test_scope_is_segment_based_and_entry_path_is_independent(path, allowed):
    entry = normalize_input("https://example.com/ru/entry")
    assert (
        scope_reason(normalize_input("https://example.com/elsewhere"), build_scope(entry)) is None
    )
    scope = build_scope(entry, path_scope=("/ru",))
    assert (scope_reason(normalize_input("https://example.com" + path), scope) is None) is allowed


def test_www_requires_an_observed_redirect_and_ports_require_permission(psl):
    entry = normalize_input("example.com/ru/")
    scope = build_scope(entry)
    target = normalize_input("https://www.example.com/ru/")
    assert scope_reason(target, scope) == "REDIRECT_EXTERNAL_HOST"
    result = decide_redirect(
        source=entry,
        status_code=301,
        location=target.url,
        scope=scope,
        psl=psl,
        visited=frozenset({entry.url}),
    )
    assert result.action == "allow_www_alias" and result.psl_sha256 == psl.sha256
    assert scope.allowed_hosts == frozenset({"example.com"})
    assert (
        scope_reason(normalize_input("https://example.com:8443/"), scope) == "TARGET_PORT_FORBIDDEN"
    )


def test_cross_scheme_cycle_stops_before_fetch(psl):
    entry = normalize_input("http://example.com/")
    result = decide_redirect(
        source=entry,
        status_code=302,
        location="https://example.com/",
        scope=build_scope(entry),
        psl=psl,
        visited=frozenset({entry.url}),
        failed_https=frozenset({"https://example.com/"}),
    )
    assert result.reason_code == "FALLBACK_NO_USABLE_RESULT"


@pytest.mark.parametrize(
    "code,status,barrier,action",
    [
        ("HTTPS_TLS_CERTIFICATE_ERROR", None, False, "http_fallback"),
        ("DNS_NXDOMAIN", None, False, "finish"),
        ("DNS_TEMPORARY", None, False, "finish"),
        (None, 403, False, "finish"),
        (None, 200, True, "finish"),
        ("NETWORK_TIMEOUT", None, False, "http_fallback"),
    ],
)
def test_next_action_table(code, status, barrier, action):
    result = choose_next_action(
        initial_url="https://example.com/",
        current_url="https://example.com/",
        status_code=status,
        error_code=code,
        allow_fallback=True,
        fallback_used=False,
        retries_used=2,
        max_retries=2,
        budget_available=True,
        access_barrier=barrier,
    )
    assert result.action == action


@pytest.mark.parametrize(
    "status,content,complete,evidence,error,fallback,diagnostic,expected",
    [
        (200, "html", True, True, None, False, False, ("ok", True)),
        (200, "html", True, True, None, True, False, ("partial", True)),
        (200, "js_candidate", True, True, None, False, False, ("partial", True)),
        (200, "suspicious_html", True, True, None, False, False, ("partial", False)),
        (200, "suspicious_html", True, True, None, False, True, ("partial", True)),
        (200, "access_barrier", True, True, None, False, False, ("partial", False)),
        (204, "empty", True, True, None, False, False, ("partial", False)),
        (200, "non_html", True, True, None, False, False, ("partial", False)),
        (403, "access_barrier", True, True, None, False, False, ("failed", False)),
        (
            302,
            "http_other",
            True,
            True,
            "REDIRECT_EXTERNAL_HOST",
            False,
            False,
            ("needs_input", False),
        ),
        (None, "not_received", False, True, "DNS_NXDOMAIN", False, False, ("failed", False)),
        (200, "html", True, False, None, False, False, ("failed", False)),
    ],
)
def test_exact_status_table(
    status, content, complete, evidence, error, fallback, diagnostic, expected
):
    result = classify_result(
        status_code=status,
        content_state=content,
        body_complete=complete,
        evidence_ready=evidence,
        terminal_error=error,
        fallback_used=fallback,
        diagnostic_allowed=diagnostic,
    )
    assert (result.status, result.can_enter_step1) == expected
