"""Bounded per-operation replay reuse; no persistent or network decision cache."""

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from typing import cast

from ..contracts import Contract

LIMIT = 64 * 1024 * 1024


@dataclass
class ReplayCache:
    entries: dict[tuple[str, str], tuple[dict[str, bytes], Contract]] = field(default_factory=dict)
    size: int = 0


CURRENT: ContextVar[ReplayCache | None] = ContextVar("steps5_8_replay", default=None)


@contextmanager
def replay_session() -> Iterator[None]:
    if CURRENT.get() is not None:
        yield
        return
    token = CURRENT.set(ReplayCache())
    try:
        yield
    finally:
        CURRENT.reset(token)


def reuse_replay[T: Contract](
    builder: Callable[[dict[str, bytes]], tuple[dict[str, bytes], T]],
) -> Callable[[dict[str, bytes]], tuple[dict[str, bytes], T]]:
    @wraps(builder)
    def wrapped(snapshots: dict[str, bytes]) -> tuple[dict[str, bytes], T]:
        cache = CURRENT.get()
        if cache is None:
            return builder(snapshots)
        sha = hashlib.sha256()
        for name, data in sorted(snapshots.items()):
            sha.update(len(name.encode()).to_bytes(8, "big"))
            sha.update(name.encode())
            sha.update(len(data).to_bytes(8, "big"))
            sha.update(data)
        key = (builder.__module__ + "." + builder.__qualname__, sha.hexdigest())
        found = cache.entries.get(key)
        if found is not None:
            artifacts, summary = found
            return dict(artifacts), cast(T, summary.model_copy(deep=True))
        artifacts, result = builder(snapshots)
        size = sum(map(len, artifacts.values()))
        if cache.size + size <= LIMIT:
            cache.entries[key] = (dict(artifacts), result.model_copy(deep=True))
            cache.size += size
        return artifacts, result

    return wrapped
