"""Pure host/port/path scope and redirect decisions. DNS checks remain separate."""

from dataclasses import dataclass, replace
from typing import Literal
from urllib.parse import unquote

from .errors import ErrorCode, GeoAuditError
from .psl import PublicSuffixList
from .urls import NormalizedURL, normalize_host, normalize_input, resolve_location


@dataclass(frozen=True)
class SiteScope:
    allowed_hosts: frozenset[str]
    path_scope: tuple[str, ...] = ()
    allowed_extra_ports: frozenset[int] = frozenset()


def build_scope(
    entry: NormalizedURL,
    *,
    allowed_hosts: tuple[str, ...] = (),
    path_scope: tuple[str, ...] = (),
    allowed_extra_ports: tuple[int, ...] = (),
) -> SiteScope:
    if any(p < 1 or p > 65535 for p in allowed_extra_ports):
        raise ValueError("invalid port")
    prefixes = []
    for path in path_scope:
        if not path.startswith("/") or "?" in path or "#" in path:
            raise ValueError("scope requires absolute path without query/fragment")
        prefixes.append(normalize_input("https://example.com" + path).entry_path)
    return SiteScope(
        frozenset({entry.host_ascii, *(normalize_host(h) for h in allowed_hosts)}),
        tuple(prefixes),
        frozenset(allowed_extra_ports),
    )


def scope_reason(url: NormalizedURL, scope: SiteScope) -> ErrorCode | None:
    if (
        url.port != (443 if url.scheme == "https" else 80)
        and url.port not in scope.allowed_extra_ports
    ):
        return "TARGET_PORT_FORBIDDEN"
    if scope.path_scope:
        path = unquote(url.entry_path)
        if not any(
            path == unquote(p).rstrip("/") or path.startswith(unquote(p).rstrip("/") + "/")
            for p in scope.path_scope
        ):
            return "TARGET_PATH_FORBIDDEN"
    if url.host_ascii not in scope.allowed_hosts:
        return "REDIRECT_EXTERNAL_HOST"
    return None


@dataclass(frozen=True)
class RedirectDecision:
    action: Literal["follow", "allow_www_alias", "needs_input", "reject"]
    target: NormalizedURL | None
    scope: SiteScope
    reason_code: ErrorCode | None
    psl_sha256: str | None = None
    downgrade: bool = False


def decide_redirect(
    *,
    source: NormalizedURL,
    status_code: int,
    location: str | None,
    scope: SiteScope,
    psl: PublicSuffixList,
    visited: frozenset[str],
    failed_https: frozenset[str] = frozenset(),
    hops: int = 0,
    max_hops: int = 5,
) -> RedirectDecision:
    if status_code not in {301, 302, 303, 307, 308} or location is None:
        return RedirectDecision("reject", None, scope, "REDIRECT_INVALID_LOCATION")
    try:
        target = resolve_location(source.url, location)
    except GeoAuditError:
        return RedirectDecision("reject", None, scope, "REDIRECT_INVALID_LOCATION")
    if target.url in failed_https:
        return RedirectDecision("reject", target, scope, "FALLBACK_NO_USABLE_RESULT")
    if target.url in visited:
        return RedirectDecision("reject", target, scope, "REDIRECT_LOOP")
    if hops >= max_hops:
        return RedirectDecision("reject", target, scope, "REDIRECT_LIMIT")
    reason = scope_reason(target, scope)
    if reason not in {None, "REDIRECT_EXTERNAL_HOST"}:
        return RedirectDecision("reject", target, scope, reason)
    downgrade = source.scheme == "https" and target.scheme == "http"
    if reason is None:
        return RedirectDecision("follow", target, scope, None, downgrade=downgrade)
    if source.host_ascii in scope.allowed_hosts and psl.is_www_pair(
        source.host_ascii, target.host_ascii
    ):
        updated = replace(scope, allowed_hosts=scope.allowed_hosts | {target.host_ascii})
        return RedirectDecision("allow_www_alias", target, updated, None, psl.sha256, downgrade)
    return RedirectDecision("needs_input", target, scope, "REDIRECT_EXTERNAL_HOST")
