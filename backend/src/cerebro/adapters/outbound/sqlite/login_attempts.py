"""Durable, instance-wide login reservations. Only admitted attempt timestamps are stored."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from cerebro.adapters.outbound.sqlite._state_db import StateDatabase
from cerebro.application.ports.system import Clock

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)
_SECOND_US = 1_000_000
_WINDOW_US = 900 * _SECOND_US


class SqliteLoginAttempts:
    def __init__(self, path: Path) -> None:
        self._db = StateDatabase(path)

    def reserve(self, clock: Clock) -> int:
        with self._db.write() as conn:
            current = (clock.now() - _EPOCH) // _MICROSECOND
            conn.execute("DELETE FROM login_attempts WHERE reserved_us <= ?", (current - _WINDOW_US,))
            count, oldest = conn.execute("SELECT count(*), min(reserved_us) FROM login_attempts").fetchone()
            if count >= 5:
                # A backwards clock step must neither reopen slots nor return more than one window.
                remaining_us = min(_WINDOW_US, int(oldest) + _WINDOW_US - current)
                return (remaining_us + _SECOND_US - 1) // _SECOND_US
            conn.execute("INSERT INTO login_attempts (reserved_us) VALUES (?)", (current,))
        return 0
