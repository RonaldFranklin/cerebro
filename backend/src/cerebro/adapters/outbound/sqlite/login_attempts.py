"""Durable, instance-wide login reservations. Store up to five admitted timestamps."""

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
            slots = conn.execute("SELECT rowid, reserved_us FROM login_attempts ORDER BY reserved_us").fetchall()
            if len(slots) >= 5:
                oldest_id, oldest_time = slots[0]
                remaining_us = int(oldest_time) + _WINDOW_US - current
                if remaining_us > 0:
                    return min(900, (remaining_us + _SECOND_US - 1) // _SECOND_US)
                # Replace only this expired slot. On rollback its newer replacement still
                # counts, so jumping ahead never discards the other four reservations.
                conn.execute("DELETE FROM login_attempts WHERE rowid = ?", (oldest_id,))
            conn.execute("INSERT INTO login_attempts (reserved_us) VALUES (?)", (current,))
        return 0
