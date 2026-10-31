"""Sequential shared budget. Monotonic time is authoritative within one process."""

import math
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .config import Step0Config
from .errors import problem
from .models import BudgetSnapshot
from .timing import TimeServices


class Budget:
    def __init__(
        self,
        config: Step0Config,
        *,
        clock: Callable[[], float] = time.monotonic,
        started_at: datetime | None = None,
        timing: TimeServices | None = None,
    ) -> None:
        self.config = config
        self._clock = timing.monotonic if timing else clock
        self._start = self._clock()
        self._offset = 0.0
        self._last_elapsed = 0.0
        self.started_at = started_at or (timing.utcnow() if timing else datetime.now(UTC))
        if self.started_at.utcoffset() != timedelta(0):
            raise ValueError("started_at must be UTC")
        self._step0_attempts = 0
        self._total_attempts = 0

    def _elapsed(self) -> float:
        elapsed = self._offset + self._clock() - self._start
        if not math.isfinite(elapsed) or elapsed < self._last_elapsed:
            raise ValueError("monotonic clock is invalid or moved backwards")
        self._last_elapsed = elapsed
        return elapsed

    @classmethod
    def restore(
        cls, config: Step0Config, snapshot: BudgetSnapshot, *, timing: TimeServices
    ) -> "Budget":
        now = timing.utcnow()
        if now < snapshot.captured_at or now >= snapshot.total_deadline_at:
            raise problem(
                "INPUT_ARTIFACT_INVALID",
                "input",
                "Run expired or UTC clock moved backwards; start a new run.",
            )
        if snapshot.total_attempts >= snapshot.total_max_attempts:
            raise problem(
                "INPUT_ARTIFACT_INVALID",
                "input",
                "Run request budget is exhausted; start a new run.",
            )
        value = cls(config, timing=timing, started_at=snapshot.started_at)
        value._offset = max(snapshot.elapsed_seconds, (now - snapshot.started_at).total_seconds())
        value._step0_attempts = snapshot.step0_attempts
        value._total_attempts = snapshot.total_attempts
        return value

    def remaining_seconds(self, *, step: int = 0) -> float:
        if step not in {0, 1}:
            raise ValueError("only steps 0 and 1 share this budget")
        limit = (
            self.config.step0_deadline_seconds if step == 0 else self.config.total_deadline_seconds
        )
        return max(0.0, limit - self._elapsed())

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
        elapsed = self._elapsed()
        return BudgetSnapshot(
            schema_version="1.2.0",
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
