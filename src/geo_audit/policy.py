"""Pure policy building blocks, independent of transport and scheduling."""

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal

from .errors import ErrorCode

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
FALLBACK_ERRORS: frozenset[ErrorCode] = frozenset(
    {
        "HTTPS_TLS_CERTIFICATE_ERROR",
        "HTTPS_TLS_HANDSHAKE_ERROR",
        "NETWORK_CONNECT_ERROR",
        "NETWORK_TIMEOUT",
        "NETWORK_READ_ERROR",
    }
)


def http_fallback_eligible(
    *,
    initial_url: str,
    error_code: ErrorCode | None,
    status_code: int | None,
    enabled: bool,
    fallback_already_used: bool,
    retries_exhausted: bool,
    budget_available: bool,
) -> bool:
    """Only a terminal transport error on the initial HTTPS candidate may switch scheme."""
    return (
        initial_url.startswith("https://")
        and enabled
        and not fallback_already_used
        and budget_available
        and status_code is None
        and error_code in FALLBACK_ERRORS
        and (error_code == "HTTPS_TLS_CERTIFICATE_ERROR" or retries_exhausted)
    )


@dataclass(frozen=True)
class NextAction:
    action: Literal["finish", "retry", "http_fallback"]
    reason_code: ErrorCode | None = None


def choose_next_action(
    *,
    initial_url: str,
    current_url: str,
    status_code: int | None,
    error_code: ErrorCode | None,
    allow_fallback: bool,
    fallback_used: bool,
    retries_used: int,
    max_retries: int,
    budget_available: bool,
    access_barrier: bool = False,
) -> NextAction:
    """One decision table, shared by future orchestration and the HTTP executor."""
    if access_barrier:
        return NextAction("finish", "ACCESS_CHALLENGE")
    if status_code is not None and 400 <= status_code < 500 and status_code not in {408, 429}:
        return NextAction("finish", error_code)
    if not budget_available:
        return NextAction("finish", "BUDGET_ATTEMPTS")
    retryable = error_code in {
        "DNS_TEMPORARY",
        "NETWORK_CONNECT_ERROR",
        "NETWORK_READ_ERROR",
        "NETWORK_TIMEOUT",
    }
    if (
        retryable or error_code is None and status_code in RETRYABLE_STATUSES
    ) and retries_used < max_retries:
        return NextAction("retry")
    if current_url == initial_url and http_fallback_eligible(
        initial_url=initial_url,
        error_code=error_code,
        status_code=status_code,
        enabled=allow_fallback,
        fallback_already_used=fallback_used,
        retries_exhausted=True,
        budget_available=budget_available,
    ):
        return NextAction("http_fallback")
    return NextAction("finish", error_code)


def retry_after_seconds(value: str | None, *, now: datetime) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if value.isascii() and value.isdecimal():
        # Reject unreasonable numbers without integer/float overflow.
        return float(value) if len(value) < 15 else float("inf")
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return None
        return max(0.0, (parsed.astimezone(UTC) - now.astimezone(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


@dataclass(frozen=True)
class StatusDecision:
    status: Literal["ok", "partial", "needs_input", "failed"]
    can_enter_step1: bool
    reason_code: ErrorCode | None


def classify_result(
    *,
    status_code: int | None,
    content_state: str,
    body_complete: bool,
    evidence_ready: bool,
    terminal_error: ErrorCode | None = None,
    fallback_used: bool = False,
    downgraded: bool = False,
    diagnostic_allowed: bool = False,
) -> StatusDecision:
    """Classify supplied observations only; never infer HTML from status 200."""
    if terminal_error and terminal_error.startswith(("INPUT_", "TARGET_", "OUTPUT_", "BUDGET_")):
        return StatusDecision("failed", False, terminal_error)
    if terminal_error == "REDIRECT_EXTERNAL_HOST":
        return StatusDecision("needs_input", False, terminal_error)
    if status_code is None:
        return StatusDecision("failed", False, terminal_error or "NETWORK_CONNECT_ERROR")
    if status_code in {401, 403}:
        return StatusDecision("failed", False, "HTTP_DENIED")
    if status_code in {404, 410}:
        return StatusDecision("failed", False, "HTTP_MISSING")
    if status_code in {408, 429} or status_code >= 500:
        return StatusDecision("failed", False, terminal_error or "HTTP_UNAVAILABLE")
    if not 200 <= status_code < 300 or status_code == 206:
        return StatusDecision("failed", False, terminal_error or "HTTP_UNEXPECTED_STATUS")
    eligible = (
        status_code == 200
        and body_complete
        and evidence_ready
        and (
            content_state in {"html", "js_candidate"}
            or content_state == "suspicious_html"
            and diagnostic_allowed
        )
    )
    if not eligible:
        if not evidence_ready:
            return StatusDecision("failed", False, "OUTPUT_WRITE_FAILED")
        reasons: dict[str, ErrorCode] = {
            "access_barrier": "ACCESS_CHALLENGE",
            "empty": "CONTENT_EMPTY",
            "non_html": "CONTENT_NON_HTML",
            "too_large": "CONTENT_TOO_LARGE",
            "suspicious_html": "CONTENT_SUSPICIOUS",
        }
        return StatusDecision(
            "partial", False, terminal_error or reasons.get(content_state, "CONTENT_INCOMPLETE")
        )
    if terminal_error is not None:
        return StatusDecision("failed", False, terminal_error)
    reason: ErrorCode | None = (
        "CONTENT_JS_CANDIDATE"
        if content_state == "js_candidate"
        else "CONTENT_SUSPICIOUS"
        if content_state == "suspicious_html"
        else "HTTPS_DOWNGRADE"
        if downgraded
        else "FALLBACK_USED"
        if fallback_used
        else None
    )
    return StatusDecision("partial" if reason else "ok", True, reason)
