"""Connection handling for ``state.sqlite``: sealed proposals, agent tokens and the owner login attempt window.

Unlike the disposable search index, this file is the only copy of the proposal audit trail and of the token
records, so nothing here ever deletes, recreates or "repairs" it: an unknown schema version or a damaged file
raises a clear error and leaves the file alone.

Integrity model: there is no rollback or replay protection for proposal rows (each is authenticated by the
cipher and bound to its id, but restoring the whole file, or one row, from an older backup passes), and token rows
are plain, unauthenticated columns protected only by the ``0600`` mode.

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

SCHEMA_VERSION: Final = 4
BUSY_TIMEOUT_MS: Final = 5000

_SCHEMA_V1: Final = """
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


_UPGRADE_V2: Final = """
ALTER TABLE proposals ADD COLUMN admission_token_id TEXT;
ALTER TABLE proposals ADD COLUMN admitted_key TEXT;
CREATE INDEX proposals_by_admission ON proposals(admission_token_id, admitted_key);
CREATE INDEX pending_by_token ON proposals(admission_token_id) WHERE status NOT IN ('rejected','applied');
"""


_UPGRADE_V3: Final = "CREATE TABLE login_attempts (reserved_us INTEGER NOT NULL) STRICT"
_UPGRADE_V4: Final = """
CREATE TABLE write_attempts (token_id TEXT NOT NULL, reserved_us INTEGER NOT NULL) STRICT;
CREATE INDEX write_attempts_by_token ON write_attempts(token_id, reserved_us);
"""


def _apply_schema(conn: sqlite3.Connection, script: str) -> None:
    # executescript would commit the surrounding migration transaction.
    for statement in script.split(";"):
        if statement.strip():
            conn.execute(statement)


def _schema_statements(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL AND name NOT GLOB 'sqlite_*'")
    }


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
            # Best effort: fold the WAL into the main file and truncate it, so replaced (sealed) row images do not
            # linger there. A concurrent reader can keep part of it until its snapshot ends.
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
        if version not in (0, 1, 2, 3):
            raise StateSchemaError(
                f"{self._path} has schema version {version}; this build understands {SCHEMA_VERSION}"
            )
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version == SCHEMA_VERSION:  # another process finished the migration
                conn.execute("COMMIT")
                return
            if version == 0:
                if conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] != 0:
                    raise StateSchemaError(f"{self._path} holds tables but no schema version; refusing to touch it")
                _apply_schema(conn, _SCHEMA_V1)
            elif version not in (1, 2, 3) or not self._matches_previous(conn, version):
                raise StateSchemaError("unexpected state schema during migration")
            if version < 2:
                _apply_schema(conn, _UPGRADE_V2)
            if version < 3:
                _apply_schema(conn, _UPGRADE_V3)
            _apply_schema(conn, _UPGRADE_V4)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    @staticmethod
    def _matches_previous(conn: sqlite3.Connection, version: int) -> bool:
        """Compare SQLite's normalized DDL against the previous application's own schema."""
        with closing(sqlite3.connect(":memory:")) as reference:
            _apply_schema(reference, _SCHEMA_V1)
            if version >= 2:
                _apply_schema(reference, _UPGRADE_V2)
            if version >= 3:
                _apply_schema(reference, _UPGRADE_V3)
            return _schema_statements(reference) == _schema_statements(conn)
