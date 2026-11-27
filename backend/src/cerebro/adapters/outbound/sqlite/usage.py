"""``usage.sqlite``: token usage counters (ADR-0031).

Disposable, unlike ``state.sqlite``: it is not backed up, it is never migrated, and a file with an unknown schema or
damaged content is discarded and recreated. Nothing here raises: the worst outcome is lost counts. The file is
``0600``, a link in its place is removed and never followed, and a busy database drops the count quickly.
"""

import contextlib
import os
import sqlite3
import stat
import threading
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from cerebro.application.dto import USAGE_HOURS, TokenUsage

SCHEMA_VERSION: Final = 1
BUSY_TIMEOUT_SECONDS: Final = 0.1
KEPT_HOURS: Final = 25
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND: Final = timedelta(microseconds=1)
_HOUR_US: Final = 3_600_000_000
_TABLES: Final = (
    "CREATE TABLE uses (token_id TEXT NOT NULL, hour INTEGER NOT NULL, count INTEGER NOT NULL, "
    "PRIMARY KEY (token_id, hour)) STRICT, WITHOUT ROWID",
    "CREATE TABLE last_use (token_id TEXT PRIMARY KEY, at_us INTEGER NOT NULL) STRICT, WITHOUT ROWID",
)
_DAMAGE: Final = frozenset({sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB})


class _Unusable(Exception):  # noqa: N818
    """The file is not a usage database of this version: discard it."""


class SqliteUsageCounters:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(os.path.abspath(path))
        self._lock = threading.Lock()

    def record(self, token_id: str, at: datetime) -> None:
        moment = (at - _EPOCH) // _MICROSECOND
        hour = moment // _HOUR_US

        def write(db: sqlite3.Connection) -> bool:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO uses VALUES (?, ?, 1) ON CONFLICT (token_id, hour) DO UPDATE SET count = count + 1",
                (token_id, hour),
            )
            db.execute(
                "INSERT INTO last_use VALUES (?, ?) ON CONFLICT (token_id) DO UPDATE "
                "SET at_us = max(at_us, excluded.at_us)",
                (token_id, moment),
            )
            db.execute("DELETE FROM uses WHERE hour <= ?", (hour - KEPT_HOURS,))
            db.execute("COMMIT")
            return True

        self._run(write)

    def summary(self, now: datetime) -> Mapping[str, TokenUsage] | None:
        current = (now - _EPOCH) // _MICROSECOND // _HOUR_US
        first = current - USAGE_HOURS + 1

        def read(db: sqlite3.Connection) -> dict[str, TokenUsage]:
            counts: dict[str, list[int]] = {}
            for token_id, hour, count in db.execute(
                "SELECT token_id, hour, count FROM uses WHERE hour BETWEEN ? AND ?", (first, current)
            ):
                counts.setdefault(token_id, [0] * USAGE_HOURS)[hour - first] = count
            return {
                token_id: TokenUsage(
                    last_used=_EPOCH + timedelta(microseconds=at_us),
                    hourly=tuple(counts.get(token_id, [0] * USAGE_HOURS)),
                )
                for token_id, at_us in db.execute("SELECT token_id, at_us FROM last_use")
            }

        return self._run(read)

    def _run[Result](self, action: Callable[[sqlite3.Connection], Result]) -> Result | None:
        if not self._lock.acquire(timeout=BUSY_TIMEOUT_SECONDS):
            return None
        try:
            # A second attempt follows only a discard, with a new empty file.
            for _ in range(2):
                try:
                    with closing(self._connect()) as db:
                        return action(db)
                except _Unusable:
                    self._discard()
                except sqlite3.DatabaseError as error:
                    if getattr(error, "sqlite_errorcode", None) not in _DAMAGE:
                        return None
                    self._discard()
            return None
        except Exception:
            return None
        finally:
            self._lock.release()

    def _connect(self) -> sqlite3.Connection:
        try:
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
        except OSError as error:
            if self._path.is_symlink():
                raise _Unusable from None
            raise error
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise OSError("not a regular file")
            if stat.S_IMODE(info.st_mode) & 0o077:
                os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        db = sqlite3.connect(self._path, isolation_level=None, timeout=BUSY_TIMEOUT_SECONDS)
        try:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("PRAGMA synchronous = NORMAL")
            self._ensure_schema(db)
        except BaseException:
            db.close()
            raise
        return db

    def _ensure_schema(self, db: sqlite3.Connection) -> None:
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version == SCHEMA_VERSION:
            return
        db.execute("BEGIN IMMEDIATE")
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version == SCHEMA_VERSION:  # another process created it
            db.execute("COMMIT")
            return
        if version != 0 or db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
            raise _Unusable
        for statement in _TABLES:
            db.execute(statement)
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        db.execute("COMMIT")

    def _discard(self) -> None:
        # ``unlink`` removes a link itself and refuses a directory.
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(f"{self._path}{suffix}")
