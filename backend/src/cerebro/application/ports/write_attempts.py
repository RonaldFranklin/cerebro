"""Durable per-credential write attempt reservations."""

from datetime import datetime
from typing import Protocol


class WriteAttemptStore(Protocol):
    def reserve(self, token_id: str, now: datetime, limit: int) -> bool:
        """Atomically reserve one rolling-hour slot for this authenticated token or return False.

        The caller supplies an aware timestamp and positive limit. Future reservations still count
        if the clock moves backwards. Refusals leave the window unchanged.
        """
        ...

    def release(self, token_id: str, now: datetime) -> None:
        """Return one reservation for this token and timestamp after a server failure."""
        ...
