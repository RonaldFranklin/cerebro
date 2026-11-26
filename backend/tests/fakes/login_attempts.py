"""In-memory instance-wide login reservation window."""

from datetime import UTC, datetime, timedelta
from math import ceil
from threading import Lock

from cerebro.application.ports.system import Clock


class FakeLoginAttempts:
    def __init__(self) -> None:
        self._reservations: list[datetime] = []
        self._lock = Lock()

    def reserve(self, clock: Clock) -> int:
        window = timedelta(minutes=15)
        with self._lock:
            now = clock.now().astimezone(UTC)
            self._reservations = [at for at in self._reservations if at > now - window]
            if len(self._reservations) >= 5:
                remaining = min(self._reservations) + window - now
                return min(900, ceil(remaining.total_seconds()))
            self._reservations.append(now)
            return 0
