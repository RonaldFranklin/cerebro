"""Disposable counters of authenticated agent requests (ADR-0031)."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from cerebro.application.dto import TokenUsage


class UsageCounters(Protocol):
    def record(self, token_id: str, at: datetime) -> None:
        """Count one request of a credential. Best effort: it returns quickly and never raises; a count that cannot
        be stored is dropped. Only the credential id, the hour and the time of the last use are kept."""
        ...

    def summary(self, now: datetime) -> Mapping[str, TokenUsage] | None:
        """The last use and the hourly counts of the 24 hours ending in the hour of ``now``, by credential id, for
        every credential ever recorded. ``None`` when the counters cannot be read."""
        ...
