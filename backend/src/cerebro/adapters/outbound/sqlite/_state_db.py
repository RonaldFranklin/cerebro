"""Connection handling for ``state.sqlite``: tokens and the login and write reservation windows.

Unlike the disposable search index, this file is the only copy of the token records, so nothing here ever deletes,
recreates, upgrades or "repairs" it: an unknown schema version (including every version written before ADR-0023) or
a damaged file raises a clear error and leaves the file alone.

Integrity model: token rows are plain, unauthenticated columns protected only by the ``0600`` mode.

Hardening: the file is created ``0600`` with ``O_NOFOLLOW`` (a symlink in its place is refused), its leaf
directory is ``0700``, WAL journaling, ``secure_delete`` and foreign keys are on, one short-lived connection per
operation, parameterized SQL only. Schema versioning uses ``PRAGMA user_version``.
"""

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from pathlib import Path
from typing import Final

SCHEMA_VERSION: Final = 5
"""Versions 1-4 belong to releases with the retired Inbox; ADR-0023 dropped them without an upgrade path."""
BUSY_TIMEOUT_MS: Final = 5000

_SCHEMA: Final = """
CREATE TABLE tokens (
    id          TEXT PRIMARY KEY,
    token_hash  TEXT NOT NULL UNIQUE,
    agent       TEXT NOT NULL,
    ceiling     TEXT NOT NULL,
    scopes      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_key TEXT NOT NULL,
    expires_at  TEXT,
    revoked_at  TEXT
) STRICT;
CREATE INDEX tokens_by_created ON tokens (created_key, id);
CREATE TABLE login_attempts (reserved_us INTEGER NOT NULL) STRICT;
CREATE TABLE write_attempts (token_id TEXT NOT NULL, reserved_us INTEGER NOT NULL) STRICT;
CREATE INDEX write_attempts_by_token ON write_attempts(token_id, reserved_us);
"""


class StateStoreError(RuntimeError):
    """``state.sqlite`` cannot be used. Messages name the path, never contents."""


class StateSchemaError(StateStoreError):
    """The file has a schema version this build does not know."""


class StateCorruptionError(StateStoreError):
    """The file or one of its rows is damaged or inconsistent. It is never deleted or repaired automatically."""


class StateFileError(StateStoreError):
    """The path is unsafe (symlink, not a regular file, permissions)."""


_CORRUPT_CODES: Final = frozenset({sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB})


def _is_corruption(error: sqlite3.DatabaseError) -> bool:
    return getattr(error, "sqlite_errorcode", None) in _CORRUPT_CODES


def is_unique_violation(error: sqlite3.IntegrityError) -> bool:
    """A duplicate key (primary key or unique index), as opposed to any other constraint failure."""
    return getattr(error, "sqlite_errorcode", None) in (
        sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
        sqlite3.SQLITE_CONSTRAINT_UNIQUE,
    )


class StateDatabase:
    """Opens ``state.sqlite`` (creating it safely on first use) and hands out one connection per operation."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(os.path.abspath(path))
        self._identity: tuple[int, int] = (0, 0)
        self._prepare_file()
        with self.read():  # validates the file and creates or checks the schema
            pass

    def __repr__(self) -> str:
        return f"StateDatabase({str(self._path)!r})"

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        with self._open() as conn:
            yield conn

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """A connection inside one ``BEGIN IMMEDIATE`` transaction (the write lock is taken up front, so a
        read-then-write sequence cannot interleave with another writer); rolled back on any exception."""
        with self._open() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            # Best effort: fold the WAL into the main file and truncate it, so replaced row images do not linger
            # there. A concurrent reader can keep part of it until its snapshot ends.
            with suppress(sqlite3.OperationalError):
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # -- internals ---------------------------------------------------------------------------------------------

    def _prepare_file(self) -> None:
        directory = self._path.parent
        try:
            if not directory.exists():
                directory.parent.mkdir(parents=True, exist_ok=True)
                directory.mkdir(mode=0o700)  # only the leaf is ours to create; existing parents are never touched
            info = directory.stat()
            if info.st_uid != os.geteuid():
                raise StateFileError(f"{directory} is not owned by the current user")
            if stat.S_IMODE(info.st_mode) & 0o077:
                raise StateFileError(f"{directory} is more open than 0700 (it is never chmod-ed automatically)")
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except OSError as error:  # includes PermissionError
            raise StateFileError(f"cannot prepare {self._path}: {error.strerror}") from None
        try:
            info = os.fstat(fd)
            self._check(info)
            self._identity = (info.st_dev, info.st_ino)
        finally:
            os.close(fd)

    def _check(self, info: os.stat_result) -> None:
        if not stat.S_ISREG(info.st_mode):
            raise StateFileError(f"{self._path} is not a regular file")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise StateFileError(f"{self._path} is more open than 0600")

    def _recheck(self) -> None:
        """On every open: the path is still the same regular file (not replaced by a symlink or another file)."""
        try:
            info = os.lstat(self._path)
        except OSError as error:
            raise StateFileError(f"cannot open {self._path}: {error.strerror}") from None
        self._check(info)
        if (info.st_dev, info.st_ino) != self._identity:
            raise StateFileError(f"{self._path} was replaced since it was opened")

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        self._recheck()
        try:
            with closing(sqlite3.connect(self._path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)) as conn:
                conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA secure_delete = ON")
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = FULL")
                self._ensure_schema(conn)
                yield conn
        except sqlite3.DatabaseError as error:
            if _is_corruption(error):
                raise StateCorruptionError(f"{self._path} is damaged or is not a SQLite database") from None
            raise

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version == SCHEMA_VERSION:
            return
        if version != 0:
            raise StateSchemaError(
                f"{self._path} has schema version {version}; this build understands only {SCHEMA_VERSION} and "
                "never upgrades or deletes it: move the file aside and issue new tokens"
            )
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version == SCHEMA_VERSION:  # another process created the schema
                conn.execute("COMMIT")
                return
            if version != 0 or conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] != 0:
                raise StateSchemaError(f"{self._path} holds tables but no known schema version; refusing to touch it")
            for statement in _SCHEMA.split(";"):  # executescript would commit the surrounding transaction
                if statement.strip():
                    conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
