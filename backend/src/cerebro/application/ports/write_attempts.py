"""Durable per-credential write attempt reservations."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol


class WriteAttemptStore(Protocol):
    def reserve(self, token_id: str, now: datetime, limit: int) -> Callable[[], None] | None:
        """Atomically reserve one rolling-hour slot for this authenticated token or return None.

        The caller supplies an aware timestamp and positive limit. Future reservations still count
        if the clock moves backwards. Refusals leave the window unchanged. Call the returned
        function once on server failure to undo the reservation, including any replaced slot.
        """
        ...
