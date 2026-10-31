"""Safe public summaries; third-party network diagnostics cannot print raw URLs/headers."""

import logging
from contextvars import ContextVar

from .urls import safe_display_url

network_logging_scope: ContextVar[bool] = ContextVar("geo_network_logging", default=False)


class _NetworkRedactor(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if network_logging_scope.get():
            record.msg = "Network diagnostic (URL, headers and exception details redacted)"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def install_network_redaction() -> None:
    for name in (
        "httpx",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
        "httpcore.socks",
    ):
        logger = logging.getLogger(name)
        if not any(isinstance(f, _NetworkRedactor) for f in logger.filters):
            logger.addFilter(_NetworkRedactor())


def public_summary(
    *,
    status: str,
    can_enter_step1: bool,
    effective_url: str | None,
    reasons: tuple[str, ...],
    next_action: str,
    run_id: str,
) -> dict[str, object]:
    return {
        "status": status,
        "can_enter_step1": can_enter_step1,
        "effective_origin": safe_display_url(effective_url) if effective_url else None,
        "reason_codes": list(reasons),
        "next_action": next_action,
        "run_id": run_id,
    }
