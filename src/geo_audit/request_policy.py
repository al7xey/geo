"""Trusted resource profiles, never a bypass of pinning, TLS or the shared Budget."""

import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from .urls import normalize_host

ResourcePurpose = Literal["html", "robots", "sitemap", "https_diagnostic", "ua_probe"]


@dataclass(frozen=True)
class RequestAuthorization:
    decision: Literal["allowed", "disallowed", "unknown"]
    reason_code: str
    evidence_ids: tuple[str, ...] = ()
    min_interval_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.decision not in {"allowed", "disallowed", "unknown"} or not self.reason_code:
            raise ValueError("invalid request authorization")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", self.reason_code):
            raise ValueError("authorization reason must be an internal code")
        if not isinstance(self.evidence_ids, tuple) or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", identity)
            for identity in self.evidence_ids
        ):
            raise ValueError("authorization evidence must be immutable internal IDs")
        if not math.isfinite(self.min_interval_seconds) or self.min_interval_seconds < 0:
            raise ValueError("invalid origin interval")


Authorizer = Callable[[str], Awaitable[RequestAuthorization]]


@dataclass(frozen=True)
class RequestPolicy:
    purpose: ResourcePurpose
    authorize: Authorizer | None = None
    max_encoded_bytes: int = 5 * 1024 * 1024
    max_decoded_bytes: int = 5 * 1024 * 1024
    allowed_sitemap_hosts: frozenset[str] = frozenset()
    user_agent: str | None = None

    def __post_init__(self) -> None:
        if self.purpose not in {"html", "robots", "sitemap", "https_diagnostic", "ua_probe"}:
            raise ValueError("unknown resource purpose")
        if self.purpose != "robots" and self.authorize is None:
            raise ValueError("a non-robots resource requires a policy authorizer")
        if self.authorize is not None and not callable(self.authorize):
            raise ValueError("authorizer must be callable")
        if self.purpose == "robots" and self.authorize is not None:
            raise ValueError("robots must not recursively require robots")
        if self.max_encoded_bytes < 1 or self.max_decoded_bytes < 1:
            raise ValueError("resource byte caps must be positive")
        if type(self.max_encoded_bytes) is not int or type(self.max_decoded_bytes) is not int:
            raise ValueError("resource caps must be integer bytes")
        if self.purpose == "robots":
            object.__setattr__(self, "max_encoded_bytes", min(self.max_encoded_bytes, 512 * 1024))
            object.__setattr__(self, "max_decoded_bytes", min(self.max_decoded_bytes, 512 * 1024))
        if not isinstance(self.allowed_sitemap_hosts, frozenset) or any(
            normalize_host(h) != h for h in self.allowed_sitemap_hosts
        ):
            raise ValueError("service hosts must be immutable canonical exact names")
        if self.user_agent is not None and (
            self.purpose != "ua_probe"
            or not self.user_agent
            or not self.user_agent.isascii()
            or any(ord(c) < 32 or ord(c) == 127 for c in self.user_agent)
        ):
            raise ValueError("only a UA probe may use a printable documented UA override")
        if self.purpose == "ua_probe" and self.user_agent is None:
            raise ValueError("UA probe requires a documented HTTP UA")
