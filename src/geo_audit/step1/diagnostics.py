"""Optional probes use the shared pinned client; rendering has no safe egress backend."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from ..content import assess_response
from ..request_policy import RequestAuthorization, RequestPolicy
from .checks import check
from .models import DiagnosticResult, TechnicalCheck
from .registry import GOOGLE_EXTENDED_PRODUCT
from .storage import encoded_json

if TYPE_CHECKING:
    from .runner import Step1Runtime


def rendered_gain(static: int, rendered: int, *, complete: bool = True) -> str:
    if min(static, rendered) < 0:
        raise ValueError("character counts must be nonnegative")
    if not complete:
        return "inconclusive"
    return (
        "observed_gain"
        if static < 1000 and rendered - static >= 500 and (static == 0 or rendered >= 2 * static)
        else "no_material_gain"
    )


def visible_words(html: str) -> tuple[frozenset[str], int]:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(["script", "style", "noscript", "template", "nav", "footer", "form"]):
        node.decompose()
    text = soup.get_text(" ", strip=True)
    return frozenset(text.casefold().split()[:10000]), len(text)


def result(
    kind: str,
    url: str | None,
    status: str,
    reason: str,
    ids: tuple[str, ...] = (),
    *,
    selection: str = "config/run capability",
    outcome: str | None = None,
    limitation: str = "Observed in our environment; not a claim about real crawler infrastructure.",
) -> DiagnosticResult:
    return DiagnosticResult.model_validate(
        dict(
            diagnostic_id="diagnostic-" + uuid.uuid4().hex,
            kind=kind,
            url=url,
            status=status,
            reason_code=reason,
            evidence_ids=ids,
            selection_reason=selection,
            outcome=outcome,
            limitation=limitation,
        )
    )


async def optional(
    runtime: Step1Runtime,
) -> tuple[tuple[DiagnosticResult, ...], tuple[TechnicalCheck, ...]]:
    diagnostics: list[DiagnosticResult] = []
    checks: list[TechnicalCheck] = []
    try:
        return await _optional(runtime, diagnostics, checks)
    finally:
        # Completed diagnostic observations survive cancellation of a subsequent probe.
        runtime.diagnostics += tuple(diagnostics)
        runtime.extra_checks += tuple(checks)


async def _optional(
    runtime: Step1Runtime, diagnostics: list[DiagnosticResult], checks: list[TechnicalCheck]
) -> tuple[tuple[DiagnosticResult, ...], tuple[TechnicalCheck, ...]]:
    crawler = runtime._crawler
    if crawler is None:
        raise ValueError("diagnostics require a started crawler")
    # No configured, independently verified destination-pinning egress adapter exists in this
    # package. Installing Playwright alone cannot grant this capability. Never launch it here.
    browser_reason = (
        "SAFE_EGRESS_UNAVAILABLE" if runtime.config.browser_enabled else "CONFIG_DISABLED"
    )
    browser_status = "unknown" if runtime.config.browser_enabled else "not_applicable"
    candidates = []
    if runtime.config.browser_enabled:
        for page in crawler.pages:
            if not any(
                d.evaluated_url == page.effective_url
                and d.product_token == runtime.robots.own_token
                and d.decision == "allowed"
                for d in runtime.robots.decisions.values()
            ):
                continue
            if page.content_state in {"access_barrier", "soft_404", "service_content"}:
                continue
            reason = (
                "JS_SHELL"
                if page.content_state == "js_candidate"
                else "EXTRACTORS_DISAGREE"
                if "EXTRACTORS_DISAGREE" in page.limitations
                else "SHORT_STATIC_TEXT"
                if not page.main_text
                or page.main_text.text_chars < runtime.config.gain_static_ceiling
                else None
            )
            if reason:
                candidates.append((page, reason))
    diagnostic_priority = {"JS_SHELL": 0, "EXTRACTORS_DISAGREE": 1, "SHORT_STATIC_TEXT": 2}
    candidates.sort(key=lambda item: (diagnostic_priority[item[1]], item[0].page_id))
    selected_browser = candidates[: runtime.config.max_browser_pages]
    for page, selection_reason in selected_browser:
        diagnostics.append(
            result(
                "browser",
                page.effective_url,
                "unknown",
                browser_reason,
                (page.evidence_id,),
                selection=selection_reason,
                outcome="inconclusive",
                limitation="Static selection only; no verified actual-peer browser egress adapter.",
            )
        )
    if not selected_browser:
        diagnostics.append(
            result(
                "browser",
                None,
                browser_status,
                browser_reason,
                outcome="inconclusive" if runtime.config.browser_enabled else None,
                limitation="Safe browser egress unavailable; successful rendering deferred.",
            )
        )
    checks.append(
        check(
            runtime,
            kind="javascript",
            status=browser_status,
            reason=browser_reason,
            observation="Rendering was not launched; JavaScript capability of the site is unknown.",
        )
    )
    if not runtime.config.ua_probes_enabled:
        diagnostics.append(result("ua_probe", None, "not_applicable", "CONFIG_DISABLED"))
        checks.append(
            check(
                runtime,
                kind="ua_comparison",
                status="not_applicable",
                reason="CONFIG_DISABLED",
                observation="UA probes disabled; no identity changes or probe GETs.",
            )
        )
        return tuple(diagnostics), tuple(checks)
    agents = [
        agent
        for agent in runtime.robots.registry.entries
        if agent.http_user_agent is not None and agent.registry_entry_state == "verified"
    ][: runtime.config.max_probe_agents]
    for agent in runtime.robots.registry.entries:
        if agent.product_token == GOOGLE_EXTENDED_PRODUCT:
            diagnostics.append(
                result(
                    "ua_probe",
                    None,
                    "not_applicable",
                    "NO_HTTP_USER_AGENT",
                    limitation="Google-Extended is a policy token, not a distinct HTTP identity.",
                )
            )
    selected = [
        p for p in crawler.pages if p.content_state == "html" and p.page_id in crawler.comparisons
    ][: runtime.config.max_probe_pages]
    for page in selected:
        baseline_words, baseline_chars = crawler.comparisons[page.page_id]
        baseline_status, baseline_types = crawler.conditions[page.page_id]
        for agent in agents:
            if runtime._stop_reason:
                diagnostics.append(
                    result("ua_probe", page.effective_url, "unknown", runtime._stop_reason)
                )
                return tuple(diagnostics), tuple(checks)

            async def authorize(url: str) -> RequestAuthorization:
                own = await crawler.authorize(url)
                if own.decision != "allowed":
                    return own
                policy = await runtime.robots.evaluate(url, agent.product_token)
                return RequestAuthorization(
                    policy.decision,
                    policy.reason_code,
                    (policy.robots_evidence_id,) if policy.robots_evidence_id else (),
                    own.min_interval_seconds,
                )

            trace = await runtime.fetch(
                page.effective_url,
                RequestPolicy("ua_probe", authorize, user_agent=agent.http_user_agent),
            )
            last = trace.attempts[-1] if trace.attempts else None
            ids = runtime.store.attempt_evidence.get(last.attempt_id, ()) if last else ()
            status, reason = "unknown", "PROBE_INCONCLUSIVE"
            observation: dict[str, object] = {
                "robot_id": agent.robot_id,
                "user_agent": agent.http_user_agent,
                "baseline_page_id": page.page_id,
                "accept_language": runtime.client.config.accept_language,
                "own_user_agent": runtime.client.config.user_agent,
                "condition_difference": "only user-agent and observation time; no cookies/profile",
                "baseline_url": page.effective_url,
                "baseline_status": baseline_status,
                "baseline_content_types": baseline_types,
                "observed_at": runtime.time.utcnow().isoformat(),
            }
            if trace.policy_denial:
                ids = trace.policy_denial.evidence_ids
                reason = trace.policy_denial.reason_code
            elif last and not trace.error and last.body_complete:
                assessment = assess_response(last)
                words, chars = visible_words(assessment.text or "")
                similarity = len(words & baseline_words) / max(1, len(words | baseline_words))
                difference = (
                    last.status_code != baseline_status
                    or tuple(
                        v.partition(";")[0].strip().lower()
                        for k, v in last.resource_headers
                        if k == "content-type"
                    )
                    != baseline_types
                    or last.requested_url != page.effective_url
                    or assessment.state != "html"
                    or similarity < 0.7
                    or abs(chars - baseline_chars) > max(500, baseline_chars // 2)
                )
                status, reason = (
                    ("warning", "UA_RESPONSE_DIFFERENCE")
                    if difference
                    else ("pass", "NO_MATERIAL_UA_DIFFERENCE")
                )
                observation.update(
                    status=last.status_code,
                    effective_url=last.requested_url,
                    content_state=assessment.state,
                    content_type=[v for k, v in last.resource_headers if k == "content-type"],
                    lexical_jaccard=similarity,
                    baseline_chars=baseline_chars,
                    probe_chars=chars,
                )
            elif trace.error:
                reason = trace.error.code
            saved = runtime.store.write_evidence(
                encoded_json(observation), representation="attempt_observation", complete=True
            )
            ids = (*ids, saved.evidence_id)
            diagnostics.append(
                result(
                    "ua_probe",
                    page.effective_url,
                    status,
                    reason,
                    ids,
                    selection="selected complete static HTML, stable selection order; "
                    + agent.robot_id,
                )
            )
            checks.append(
                check(
                    runtime,
                    kind="ua_comparison",
                    status=status,
                    reason=reason,
                    observation=(
                        "Status/URL/type/challenge and lexical/length difference; not hash alone."
                    ),
                    page=page,
                    evidence=ids,
                    robot=agent.robot_id,
                )
            )
    if not selected:
        diagnostics.append(result("ua_probe", None, "unknown", "NO_ELIGIBLE_PAGES"))
    return tuple(diagnostics), tuple(checks)
