"""Connection handling for ``state.sqlite``: proposals (sealed) and agent tokens.

Unlike the disposable search index, this file is the only copy of the proposal audit trail and of the token
records, so nothing here ever deletes, recreates or "repairs" it: an unknown schema version or a damaged file
raises a clear error and leaves the file alone.

Hardening: the file is created ``0600`` with ``O_NOFOLLOW`` (a symlink in its place is refused), its leaf
directory is ``0700``, WAL journaling, ``secure_delete`` and foreign keys are on, one short-lived connection per
operation, parameterized SQL only. Schema versioning uses ``PRAGMA user_version``.
"""

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Final

SCHEMA_VERSION: Final = 1
BUSY_TIMEOUT_MS: Final = 5000

_SCHEMA: Final = """
CREATE TABLE proposals (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    status       TEXT NOT NULL,
    revision     INTEGER NOT NULL CHECK (revision >= 0),
    source_agent TEXT NOT NULL,
    created_key  TEXT NOT NULL,
    updated_key  TEXT NOT NULL,
    curated_by   TEXT,
    approved_by  TEXT,
    payload      BLOB NOT NULL
) STRICT;
CREATE INDEX proposals_by_status ON proposals (status, created_key, id);
CREATE INDEX proposals_by_agent ON proposals (source_agent, created_key, id);
CREATE INDEX proposals_by_created ON proposals (created_key, id);
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
"""


class StateStoreError(RuntimeError):
    """``state.sqlite`` cannot be used. Messages name the path, never contents."""


class StateSchemaError(StateStoreError):
    """The file has a schema version this build does not know. It is never migrated or deleted automatically."""


class StateCorruptionError(StateStoreError):
    """The file or one of its rows is damaged or inconsistent. It is never deleted or repaired automatically."""


class StateFileError(StateStoreError):
    """The path is unsafe (symlink, not a regular file, permissions)."""


_CORRUPT_CODES: Final = frozenset({sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB})


def _is_corruption(error: sqlite3.DatabaseError) -> bool:
    return getattr(error, "sqlite_errorcode", None) in _CORRUPT_CODES


class StateDatabase:
    """Opens ``state.sqlite`` (creating it safely on first use) and hands out one connection per operation."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(os.path.abspath(path))
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

    # -- internals ---------------------------------------------------------------------------------------------

    def _prepare_file(self) -> None:
        directory = self._path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if stat.S_IMODE(directory.stat().st_mode) & 0o077:
            directory.chmod(0o700)
        try:
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except OSError as error:
            raise StateFileError(f"cannot open {self._path}: {error.strerror}") from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise StateFileError(f"{self._path} is not a regular file")
            if stat.S_IMODE(info.st_mode) & 0o077:
                raise StateFileError(f"{self._path} is more open than 0600")
        finally:
            os.close(fd)

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
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
                f"{self._path} has schema version {version}; this build understands {SCHEMA_VERSION}"
            )
        conn.execute("BEGIN IMMEDIATE")
        try:
            if int(conn.execute("PRAGMA user_version").fetchone()[0]) == 0:  # lost the race otherwise
                if conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] != 0:
                    raise StateSchemaError(f"{self._path} holds tables but no schema version; refusing to touch it")
                self._create(conn)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    @staticmethod
    def _create(conn: sqlite3.Connection) -> None:
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
