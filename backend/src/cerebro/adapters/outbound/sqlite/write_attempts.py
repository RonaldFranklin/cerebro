"""Rolling-hour write attempt limits. Only credential ids and reservation times enter SQLite."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from cerebro.adapters.outbound.sqlite._state_db import StateDatabase

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)
_HOUR_US = 3_600_000_000


class SqliteWriteAttemptStore:
    def __init__(self, path: Path | str) -> None:
        self._db = StateDatabase(path)

    def reserve(self, token_id: str, now: datetime, limit: int) -> bool:
        current = (now - _EPOCH) // _MICROSECOND
        with self._db.write() as db:
            total, active = db.execute(
                "SELECT count(*), count(*) FILTER (WHERE reserved_us > ?) FROM write_attempts WHERE token_id = ?",
                (current - _HOUR_US, token_id),
            ).fetchone()
            if active >= limit:
                return False
            if total >= limit:
                # Replace one expired slot. A clock rewind must not discard all previous reservations.
                db.execute(
                    "DELETE FROM write_attempts WHERE rowid = (SELECT rowid FROM write_attempts "
                    "WHERE token_id = ? ORDER BY reserved_us LIMIT 1)",
                    (token_id,),
                )
            db.execute("INSERT INTO write_attempts (token_id, reserved_us) VALUES (?, ?)", (token_id, current))
            return True

    def release(self, token_id: str, reserved_at: datetime) -> None:
        reserved = (reserved_at - _EPOCH) // _MICROSECOND
        with self._db.write() as db:
            db.execute(
                "DELETE FROM write_attempts WHERE rowid = (SELECT rowid FROM write_attempts "
                "WHERE token_id = ? AND reserved_us = ? LIMIT 1)",
                (token_id, reserved),
            )
