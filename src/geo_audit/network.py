"""Orchestration boundary, implemented by SafeHttpClient.

The IP-pinned adapter lives in connection.py; api.py owns evidence and classification.
"""

from typing import Literal, Protocol

from .budget import Budget
from .http_client import FetchTrace, HttpObserver
from .scope import SiteScope


class Step0Client(Protocol):
    async def get(
        self,
        url: str,
        *,
        scope: SiteScope,
        budget: Budget,
        step: Literal[0, 1] = 0,
        allow_http_fallback: bool = False,
        observer: HttpObserver | None = None,
    ) -> FetchTrace:
        """Shared GET policy; caller commits its returned bytes before publishing site.json."""
        ...
