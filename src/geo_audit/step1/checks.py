"""Observed metadata and Google-scoped directive semantics; no implied crawl permissions."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from itertools import islice
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..request_policy import RequestPolicy
from ..urls import normalize_input
from .models import DiagnosticResult, DirectiveObservation, Page, Signal, TechnicalCheck

if TYPE_CHECKING:
    from .runner import Step1Runtime

RULE_VERSION = "static-checks-1-google-2026-11-01"
ARG_DIRECTIVES = frozenset(
    {"max-snippet", "max-image-preview", "max-video-preview", "unavailable_after"}
)


@dataclass(frozen=True)
class Metadata:
    directives: tuple[tuple[str, str | None, str, tuple[str, ...]], ...]
    signals: tuple[tuple[str, str], ...]
    truncated: bool


def header_directives(raw: str) -> list[tuple[str | None, tuple[str, ...]]]:
    groups: list[tuple[str | None, list[str]]] = [(None, [])]
    for part in raw.split(","):
        part = part.strip().lower()
        prefix, colon, tail = part.partition(":")
        if colon and prefix not in ARG_DIRECTIVES and re.fullmatch(r"[a-z][a-z0-9_-]*", prefix):
            groups.append((prefix, []))
            part = tail.strip()
        if part:
            groups[-1][1].append(part)
    return [(recipient, tuple(tokens)) for recipient, tokens in groups if tokens]


def capture(soup: BeautifulSoup, headers: tuple[tuple[str, str], ...]) -> Metadata:
    records: list[tuple[str, str | None, str, tuple[str, ...]]] = []
    signals: list[tuple[str, str]] = []
    for name, value in headers:
        if name.lower() == "x-robots-tag":
            records.extend(
                ("http_header", recipient, value, tokens)
                for recipient, tokens in header_directives(value)
            )
    metas = soup.find_all("meta", limit=2001)
    links = soup.find_all("link", limit=2001)
    hidden = soup.find_all(True, attrs={"data-nosnippet": True}, limit=2001)
    for meta in metas[:2000]:
        name = str(meta.get("name", "")).lower()
        if (
            name == "robots"
            or "bot" in name
            or name in {"google-extended", "chatgpt-user", "claude-user"}
        ):
            raw = str(meta.get("content", ""))[:8192]
            records.append(
                (
                    "html_meta",
                    None if name == "robots" else name,
                    raw,
                    tuple(t.strip().lower() for t in raw.split(",") if t.strip()),
                )
            )
    for element in hidden[:2000]:
        records.append(
            ("data_nosnippet", None, element.get_text(" ", strip=True)[:2048], ("data-nosnippet",))
        )
    for link in links[:2000]:
        rel = link.get_attribute_list("rel")
        if "canonical" in rel:
            records.append(("canonical", None, str(link.get("href", ""))[:8192], ()))
        if "alternate" in rel and link.get("hreflang"):
            signals.append(
                (
                    "hreflang",
                    str(link.get("hreflang"))[:128] + " " + str(link.get("href", ""))[:2048],
                )
            )
    if soup.html and soup.html.get("lang"):
        signals.append(("lang", str(soup.html.get("lang"))[:128]))
    return Metadata(
        tuple(records), tuple(signals), any(len(nodes) > 2000 for nodes in (metas, links, hidden))
    )


def metadata(
    runtime: Step1Runtime,
    page: Page,
    facts: Metadata,
    text: str,
    header_evidence: str | None = None,
) -> Page:
    from .extraction import quote

    directives = tuple(
        DirectiveObservation.model_validate(
            dict(
                source=source,
                representation="static",
                evidence_id=header_evidence
                if source == "http_header" and header_evidence
                else page.evidence_id,
                recipient=recipient,
                raw=raw,
                tokens=tokens,
            )
        )
        for source, recipient, raw, tokens in facts.directives
    )
    signals = [
        Signal.model_validate(
            dict(
                kind=kind,
                value=value,
                strength="explicit",
                quote=None,
                evidence_id=page.evidence_id,
            )
        )
        for kind, value in facts.signals
    ]
    if page.main_text:
        for pattern, kind, strength in [
            (r"(?im)\b(?:address|адрес)\s*:[^\n]{5,240}", "address", "explicit"),
            (
                r"(?im)\b(?:we serve|service area|работаем в|территория обслуживания)"
                r"\s*:?[^\n]{3,240}",
                "service_geography",
                "explicit",
            ),
            (r"(?<!\w)\+\d[\d ()-]{8,24}\d", "phone", "weak"),
        ]:
            for match in islice(re.finditer(pattern, text), 100):
                signals.append(
                    Signal.model_validate(
                        dict(
                            kind=kind,
                            value=match.group(),
                            strength=strength,
                            quote=quote(text, page.main_text, *match.span()),
                            evidence_id=page.main_text.evidence_id,
                        )
                    )
                )
    host = normalize_input(page.effective_url).host_ascii
    signals.append(
        Signal(
            kind="tld",
            value=host.rsplit(".", 1)[-1],
            strength="weak",
            quote=None,
            evidence_id=page.evidence_id,
        )
    )
    return page.model_copy(
        update={
            "directives": directives,
            "signals": tuple(signals),
            "limitations": (*page.limitations, "METADATA_TRUNCATED")
            if facts.truncated
            else page.limitations,
        }
    )


def check(
    runtime: Step1Runtime,
    *,
    kind: str,
    status: str,
    reason: str,
    observation: str,
    url: str | None = None,
    page: Page | None = None,
    evidence: tuple[str, ...] = (),
    robot: str | None = None,
    limitation: str = "Observed static response only; no visibility or indexing guarantee.",
) -> TechnicalCheck:
    return TechnicalCheck.model_validate(
        dict(
            check_id="check-" + uuid.uuid4().hex,
            kind=kind,
            status=status,
            reason_code=reason,
            observation=observation,
            url=url or (page.effective_url if page else None),
            page_id=page.page_id if page else None,
            robot_id=robot,
            evidence_ids=evidence or ((page.evidence_id,) if page else ()),
            decisive_rule_lines=(),
            limitation=limitation,
            observed_at=runtime.time.utcnow(),
            rule_version=RULE_VERSION,
            scope="Google Search" if robot == "googlebot" else "this run / observed response",
        )
    )


def page_checks(
    runtime: Step1Runtime,
    page: Page,
    status: int,
    tls: str,
    observation_evidence: str | None = None,
) -> tuple[TechnicalCheck, ...]:
    site = runtime.handoff.contract.site
    access_evidence = (
        (observation_evidence,)
        if observation_evidence
        else tuple(
            e.evidence_id
            for e in runtime.store.records
            if e.representation == "attempt_observation"
            and e.original_evidence_id == site.selected_evidence_id
        )
    )
    generic = [
        d
        for d in page.directives
        if d.source in {"html_meta", "http_header"} and d.recipient in {None, "googlebot"}
    ]
    tokens = {token for d in generic for token in d.tokens}
    directive_evidence = tuple(dict.fromkeys([page.evidence_id, *(d.evidence_id for d in generic)]))
    deny = bool(tokens & {"noindex", "none"})
    complete = (
        page.header_capture_complete
        and page.extraction_method is not None
        and "METADATA_TRUNCATED" not in page.limitations
        and page.content_state
        not in {
            "incomplete",
            "too_large",
            "access_barrier",
            "soft_404",
            "non_html",
        }
    )
    result = [
        check(
            runtime,
            kind="indexing_directives",
            status="fail" if deny else "pass" if complete else "unknown",
            reason="NOINDEX_OBSERVED"
            if deny
            else "NO_EXPLICIT_NOINDEX_OBSERVED"
            if complete
            else "OBSERVATION_INCOMPLETE",
            observation="Google most-restrictive policy; common/addressed directives preserved.",
            evidence=directive_evidence,
            page=page,
            robot="googlebot",
        )
    ]
    addressed = {d.recipient for d in page.directives if d.recipient and d.recipient != "googlebot"}
    for recipient in sorted(addressed):
        result.append(
            check(
                runtime,
                kind="indexing_directives",
                status="unknown",
                reason="OPERATOR_SEMANTICS_UNVERIFIED",
                observation="Addressed directives retained without assumed conflict resolution.",
                page=page,
                robot=recipient,
                evidence=tuple(
                    dict.fromkeys(
                        [
                            page.evidence_id,
                            *(d.evidence_id for d in page.directives if d.recipient == recipient),
                        ]
                    )
                ),
            )
        )
    snippets = any(
        t == "nosnippet"
        or t.startswith(("max-snippet:", "max-image-preview:", "max-video-preview:"))
        for t in tokens
    )
    snippets |= any(d.source == "data_nosnippet" for d in page.directives)
    result.append(
        check(
            runtime,
            kind="snippet_directives",
            status="warning" if snippets else "pass" if complete else "unknown",
            reason="SNIPPET_RESTRICTION_OBSERVED"
            if snippets
            else "NO_SNIPPET_RESTRICTION_OBSERVED"
            if complete
            else "OBSERVATION_INCOMPLETE",
            observation="Snippet controls apply to supporting products, not a universal AI ban.",
            evidence=directive_evidence,
            page=page,
            robot="googlebot",
        )
    )
    canonicals = [d.raw for d in page.directives if d.source == "canonical"]
    result.append(
        check(
            runtime,
            kind="canonical_signal",
            status="warning" if canonicals else "unknown",
            reason="CANONICAL_DECLARED" if canonicals else "CANONICAL_NOT_OBSERVED",
            observation="Publisher canonical declarations: "
            + repr(canonicals)[:2048]
            + "; no automatic GET or scope permission.",
            page=page,
        )
    )
    result.append(
        check(
            runtime,
            kind="observed_access",
            status="pass" if 200 <= status < 300 else "fail",
            reason="HTTP_RESPONSE_OBSERVED",
            evidence=access_evidence,
            observation=f"HTTP {status} from our environment",
            page=page,
        )
    )
    failed_certificate = page.page_id == "page-00001" and any(
        a.tls_state == "certificate_error" for a in site.attempts
    )
    failed_attempts = {a.attempt_id for a in site.attempts if a.tls_state == "certificate_error"}
    failed_records = {r.evidence_id for r in site.evidence if r.attempt_id in failed_attempts}
    tls_evidence = tuple(
        e.evidence_id
        for e in runtime.store.records
        if failed_certificate
        and e.representation == "attempt_observation"
        and e.original_evidence_id in failed_records
    )
    result.append(
        check(
            runtime,
            kind="https_transport",
            evidence=tls_evidence if failed_certificate else access_evidence,
            status="fail"
            if failed_certificate
            else "pass"
            if tls == "verified"
            else "warning"
            if "HTTPS_DOWNGRADE" in site.reason_codes
            else "unknown",
            reason="CERTIFICATE_FAILURE_OBSERVED"
            if failed_certificate
            else "TLS_VERIFIED"
            if tls == "verified"
            else "HTTPS_NOT_ESTABLISHED",
            observation=(
                "TLS is separate from HTTP access; timeout does not prove HTTPS is absent."
            ),
            page=page,
        )
    )
    return tuple(result)


async def https_alternative(runtime: Step1Runtime) -> tuple[DiagnosticResult, ...]:
    site = runtime.handoff.contract.site
    url = site.effective_url
    if (
        not url
        or urlsplit(url).scheme != "http"
        or any(a.requested_url.startswith("https:") for a in site.attempts)
    ):
        return ()
    parts = urlsplit(url)
    reason, status = "NONSTANDARD_PORT_MAPPING_REQUIRED", "unknown"
    ids: tuple[str, ...] = ()
    target = None
    if parts.port in {None, 80}:
        host = normalize_input(url).host_ascii
        authority = "[" + host + "]" if ":" in host else host
        target = (
            "https://"
            + authority
            + (parts.path or "/")
            + ("?" + parts.query if parts.query else "")
        )
        trace = await runtime.fetch(
            target, RequestPolicy("https_diagnostic", runtime.robots.authorize)
        )
        last = trace.attempts[-1] if trace.attempts else None
        ids = runtime.store.attempt_evidence.get(last.attempt_id, ()) if last else ()
        if trace.policy_denial:
            ids, reason = trace.policy_denial.evidence_ids, trace.policy_denial.reason_code
        elif last and last.tls_state == "verified":
            status, reason = "pass", "TLS_VERIFIED"
        else:
            reason = trace.error.code if trace.error else "HTTPS_UNOBSERVED"
            if reason == "HTTPS_TLS_CERTIFICATE_ERROR":
                status = "fail"
        if last and last.status_code is not None:
            runtime.extra_checks += (
                check(
                    runtime,
                    kind="observed_access",
                    status="pass" if 200 <= last.status_code < 300 else "fail",
                    reason="HTTPS_DIAGNOSTIC_HTTP_ACCESS",
                    url=target,
                    evidence=ids,
                    observation=(
                        f"HTTPS diagnostic HTTP {last.status_code}; "
                        "TLS separate; body excluded from corpus."
                    ),
                ),
            )
    return (
        DiagnosticResult.model_validate(
            dict(
                diagnostic_id="diagnostic-" + uuid.uuid4().hex,
                kind="https_alternative",
                url=target,
                status=status,
                reason_code=reason,
                evidence_ids=ids,
                selection_reason="Explicit HTTP without HTTPS evidence",
                outcome=None,
                limitation=(
                    "HTTP80 maps to HTTPS443 only; other ports require explicit mapping. "
                    "Body excluded from HTTP corpus; access and TLS differ."
                ),
            )
        ),
    )
