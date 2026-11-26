"""Rolling-hour write attempt limits. Only credential ids and reservation times enter SQLite."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cerebro.adapters.outbound.sqlite._state_db import StateDatabase

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)
_HOUR_US = 3_600_000_000


class SqliteWriteAttemptStore:
    def __init__(self, path: Path | str) -> None:
        self._db = StateDatabase(path)

    def reserve(self, token_id: str, now: datetime, limit: int) -> Callable[[], None] | None:
        current = (now - _EPOCH) // _MICROSECOND
        with self._db.write() as db:
            total, active = db.execute(
                "SELECT count(*), count(*) FILTER (WHERE reserved_us > ?) FROM write_attempts WHERE token_id = ?",
                (current - _HOUR_US, token_id),
            ).fetchone()
            if active >= limit:
                return None
            previous: int | None = None
            if total >= limit:
                row_id, previous = db.execute(
                    "SELECT rowid, reserved_us FROM write_attempts WHERE token_id = ? ORDER BY reserved_us LIMIT 1",
                    (token_id,),
                ).fetchone()
                db.execute("UPDATE write_attempts SET reserved_us = ? WHERE rowid = ?", (current, row_id))
            else:
                row_id = db.execute(
                    "INSERT INTO write_attempts (token_id, reserved_us) VALUES (?, ?)", (token_id, current)
                ).lastrowid

        def release() -> None:
            # A late refund must leave any subsequent reservation of this slot intact.
            with self._db.write() as db:
                if previous is None:
                    db.execute(
                        "DELETE FROM write_attempts WHERE rowid = ? AND token_id = ? AND reserved_us = ?",
                        (row_id, token_id, current),
                    )
                else:
                    db.execute(
                        "UPDATE write_attempts SET reserved_us = ? WHERE rowid = ? "
                        "AND token_id = ? AND reserved_us = ?",
                        (previous, row_id, token_id, current),
                    )

        return release
