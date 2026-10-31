"""Injectable monotonic/wall clocks, sleeper and retry jitter."""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class TimeServices:
    monotonic: Callable[[], float] = time.monotonic
    utcnow: Callable[[], datetime] = utc_now
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    jitter: Callable[[float, float], float] = random.uniform
