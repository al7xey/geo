"""Orchestration boundary, implemented by SafeHttpClient.

The IP-pinned HTTPX/HTTP Core adapter lives in connection.py. Publishing evidence
and classifying HTML remain responsibilities of the future full step 0 runner.
"""

from typing import Literal, Protocol

from .budget import Budget
from .http_client import FetchTrace
from .models import ResolvedTarget
from .scope import SiteScope


class DestinationChecker(Protocol):
    async def check(self, url: str, budget: Budget) -> ResolvedTarget: ...


class Step0Client(Protocol):
    async def get(
        self,
        url: str,
        *,
        scope: SiteScope,
        budget: Budget,
        step: Literal[0, 1] = 0,
        allow_http_fallback: bool = False,
    ) -> FetchTrace:
        """Shared GET policy; caller commits its returned bytes before publishing site.json."""
        ...


class AliasVerifier(Protocol):
    def is_observed_www_alias(self, source: str, target: str, psl_sha256: str) -> bool:
        """Use a local PSL including private suffixes; never fetch PSL during an audit."""
        ...
