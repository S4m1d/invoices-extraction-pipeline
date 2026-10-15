"""Daily LLM spend limit (see fields-extractor/specs/overview.md, Daily budget)."""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from .llm import LlmDailyBudget

log = logging.getLogger(__name__)


class UsageStore(Protocol):
    def list_usage(self, day: str) -> list[dict]:
        """All counters of the day (one per instance). Raises StorageUnavailable."""

    def put_usage(self, day: str, instance: str, counter: dict) -> None:
        """Write this instance's counter of the day. Raises StorageUnavailable."""


class DailyBudget:
    def __init__(
        self,
        store: UsageStore,
        limit_usd: Decimal,
        instance: str,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ):
        self.store = store
        self.limit = limit_usd
        self.instance = instance
        self.today = today
        self._lock = threading.Lock()
        self._day: str | None = None
        self._counter: dict = {}
        self._dirty = False

    def spent_today(self) -> Decimal:
        """Spend of all instances today. Raises StorageUnavailable if it cannot be read."""
        day = self.today().isoformat()
        counters = self.store.list_usage(day)
        others = [c for c in counters if c.get("instance") != self.instance]
        stored = [c for c in counters if c.get("instance") == self.instance]
        with self._lock:
            mine = self._own(day)
            if not mine["requests"] and stored:  # first use in this process: continue the stored counter
                self._counter = {**mine, **stored[0]}
                mine = self._counter
            return sum((Decimal(c["spent_usd"]) for c in others), Decimal(0)) + Decimal(mine["spent_usd"])

    def ensure_available(self) -> None:
        spent = self.spent_today()
        if spent >= self.limit:
            raise LlmDailyBudget(f"daily budget reached: spent ${spent:.4f} of ${self.limit:.2f} today (UTC)")

    def available(self) -> bool:
        """Check while paused: True when today's spend is below the budget again."""
        return self.spent_today() < self.limit

    def add(self, cost: Decimal, input_tokens: int, output_tokens: int) -> None:
        """Record one request. A failed write is kept in memory and retried with the next one."""
        day = self.today().isoformat()
        with self._lock:
            c = self._own(day)
            c["spent_usd"] = str((Decimal(c["spent_usd"]) + cost).quantize(Decimal("0.000001")))
            c["input_tokens"] += input_tokens
            c["output_tokens"] += output_tokens
            c["requests"] += 1
            snapshot = dict(c)
        try:
            self.store.put_usage(day, self.instance, snapshot)
        except Exception as e:  # StorageUnavailable or anything else: never lose the amount
            log.error("cannot write daily spend counter, kept in memory", extra={"error": str(e), "day": day})

    def _own(self, day: str) -> dict:
        """This instance's counter for `day` (caller holds the lock); a new day starts at zero."""
        if self._day != day:
            self._day = day
            self._counter = {
                "date": day,
                "instance": self.instance,
                "spent_usd": "0",
                "input_tokens": 0,
                "output_tokens": 0,
                "requests": 0,
            }
        return self._counter
