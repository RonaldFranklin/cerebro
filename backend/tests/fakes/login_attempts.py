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
            available_at = max(self._reservations, default=now)
            if len(self._reservations) >= 5:
                available_at = max(available_at, min(self._reservations) + window)
            if now < available_at:
                return min(900, ceil((available_at - now).total_seconds()))
            self._reservations.append(now)
            return 0
