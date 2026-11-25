"""Shared plumbing of ``index.sqlite``: file creation, schema versioning, connections and transactions.

Rules (``docs/plans/phase-2.md`` section 3):

* The file is created ``0600`` inside a ``0700`` directory (it holds decrypted note text and is never backed up).
  A looser mode on an existing file is repaired to ``0600``.
* One connection per operation, WAL journal, schema version in ``PRAGMA user_version``.
* The index is disposable: a corrupt file or a different schema version is deleted and recreated empty (the
  recorded revision is then ``None``, which makes the container reindex). There are no migrations.
"""

import contextlib
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from cerebro.domain.access import AccessCeiling
from cerebro.domain.enums import Sensitivity, Status
from cerebro.domain.errors import InvalidValueError

SCHEMA_VERSION: Final = 1
_BUSY_TIMEOUT_MS: Final = 5000

_SCHEMA: Final = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID",
    """CREATE TABLE notes (
        id INTEGER PRIMARY KEY,
        note_id TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        summary TEXT NOT NULL,
        area TEXT NOT NULL,
        type TEXT NOT NULL,
        priority TEXT NOT NULL,
        sensitivity TEXT NOT NULL,
        sens_rank INTEGER NOT NULL,
        status TEXT NOT NULL,
        terms TEXT NOT NULL
    )""",
    "CREATE VIRTUAL TABLE notes_fts USING fts5(title, summary, body, tokenize = 'porter unicode61')",
    """CREATE TABLE graph_nodes (
        note_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        type TEXT NOT NULL,
        priority TEXT NOT NULL,
        sensitivity TEXT NOT NULL,
        sens_rank INTEGER NOT NULL,
        status TEXT NOT NULL,
        hub TEXT,
        superseded_by TEXT,
        related TEXT NOT NULL,
        body_links TEXT NOT NULL,
        loose_body_targets TEXT NOT NULL
    ) WITHOUT ROWID""",
    "CREATE TABLE graph_edges (source TEXT NOT NULL, target TEXT NOT NULL, kind TEXT NOT NULL, section TEXT NOT NULL)",
    "CREATE INDEX graph_edges_source ON graph_edges (source)",
    "CREATE INDEX graph_edges_target ON graph_edges (target)",
)


class IndexUnavailableError(RuntimeError):
    """The SQLite index cannot be used (for example, this SQLite build has no FTS5)."""


def _require_fts5() -> None:
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE probe USING fts5(x, tokenize = 'porter unicode61')")
    except sqlite3.Error as error:
        raise IndexUnavailableError(
            "this SQLite build has no FTS5 (with the 'porter' and 'unicode61' tokenizers); Cérebro needs it for search"
        ) from error
    finally:
        probe.close()


def _create_private_file(path: Path) -> None:
    """Create ``path`` (empty) as ``0600`` if absent; repair a looser mode."""
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.close(descriptor)
    path.chmod(0o600)


def _delete_files(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _open(path: Path) -> sqlite3.Connection:
    # Autocommit: transactions are explicit (see ``transaction``). One connection per operation, so the default
    # same-thread check is right.
    connection = sqlite3.connect(path, isolation_level=None, timeout=_BUSY_TIMEOUT_MS / 1000)
    connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def _initialise(path: Path) -> None:
    connection = _open(path)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        fresh = path.stat().st_size == 0
        if not fresh and version != SCHEMA_VERSION:
            raise sqlite3.DatabaseError("index schema version differs")
        if fresh:
            connection.execute("PRAGMA journal_mode = WAL")
            with transaction(connection):
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        else:
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        connection.close()


def prepare(path: Path) -> None:
    """Make ``path`` a usable, current-version index file (creating or recreating it as needed)."""
    _require_fts5()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _create_private_file(path)
    try:
        _initialise(path)
    except sqlite3.OperationalError as error:
        if "locked" in str(error) or "busy" in str(error):
            raise
        _recreate(path)
    except sqlite3.DatabaseError:
        _recreate(path)


def _recreate(path: Path) -> None:
    _delete_files(path)
    _create_private_file(path)
    _initialise(path)


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    connection = _open(path)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """``BEGIN IMMEDIATE`` ... ``COMMIT``; ``ROLLBACK`` on any exception (the previous state stays intact)."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def read_revision(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def write_revision(connection: sqlite3.Connection, key: str, revision: str | None) -> None:
    if revision is None:
        connection.execute("DELETE FROM meta WHERE key = ?", (key,))
    else:
        connection.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, revision))


_SENSITIVITY_RANK: Final = {level: rank for rank, level in enumerate(Sensitivity)}
_HISTORY: Final = tuple(status.value for status in Status if not status.is_current)


def sensitivity_rank(sensitivity: Sensitivity) -> int:
    return _SENSITIVITY_RANK[sensitivity]


def visibility(alias: str, ceiling: AccessCeiling, *, include_history: bool) -> tuple[str, list[str | int]]:
    """SQL predicate and parameters for "visible under ``ceiling``" on a table with ``sens_rank`` and ``status``.

    This is the only place a ceiling becomes SQL; it mirrors ``cerebro.domain.access.is_visible``. Fails closed on a
    value that is not an ``AccessCeiling``.
    """
    if not isinstance(ceiling, AccessCeiling):
        raise InvalidValueError("ceiling", "expected an AccessCeiling")
    predicate = f"{alias}.sens_rank <= ?"
    params: list[str | int] = [sensitivity_rank(ceiling.level)]
    if include_history is not True:
        predicate += f" AND {alias}.status NOT IN ({', '.join('?' * len(_HISTORY))})"
        params.extend(_HISTORY)
    return predicate, params
