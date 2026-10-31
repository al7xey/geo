"""Sequential shared budget. Monotonic time is authoritative within one process."""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .config import Step0Config
from .errors import problem
from .models import BudgetSnapshot


class Budget:
    def __init__(
        self,
        config: Step0Config,
        *,
        clock: Callable[[], float] = time.monotonic,
        started_at: datetime | None = None,
    ) -> None:
        self.config = config
        self._clock = clock
        self._start = clock()
        self.started_at = started_at or datetime.now(UTC)
        if self.started_at.utcoffset() != timedelta(0):
            raise ValueError("started_at must be UTC")
        self._step0_attempts = 0
        self._total_attempts = 0

    def remaining_seconds(self, *, step: int = 0) -> float:
        limit = (
            self.config.step0_deadline_seconds if step == 0 else self.config.total_deadline_seconds
        )
        return max(0.0, limit - (self._clock() - self._start))

    def check(self, *, step: int = 0) -> None:
        if self.remaining_seconds(step=step) <= 0:
            raise problem("BUDGET_DEADLINE", "budget", "Time budget exhausted.")
        if self._total_attempts >= self.config.total_max_attempts or (
            step == 0 and self._step0_attempts >= self.config.step0_max_attempts
        ):
            raise problem("BUDGET_ATTEMPTS", "budget", "Request budget exhausted.")

    def reserve_attempt(self, *, step: int = 0) -> None:
        """Charge immediately before starting transport, including connection failures."""
        self.check(step=step)
        self._total_attempts += 1
        if step == 0:
            self._step0_attempts += 1

    def snapshot(self) -> BudgetSnapshot:
        elapsed = max(0.0, self._clock() - self._start)
        return BudgetSnapshot(
            schema_version="1.0.0",
            started_at=self.started_at,
            captured_at=self.started_at + timedelta(seconds=elapsed),
            step0_deadline_at=self.started_at
            + timedelta(seconds=self.config.step0_deadline_seconds),
            total_deadline_at=self.started_at
            + timedelta(seconds=self.config.total_deadline_seconds),
            elapsed_seconds=elapsed,
            step0_attempts=self._step0_attempts,
            total_attempts=self._total_attempts,
            step0_max_attempts=self.config.step0_max_attempts,
            total_max_attempts=self.config.total_max_attempts,
        )
