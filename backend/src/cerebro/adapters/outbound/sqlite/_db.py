"""Shared plumbing of ``index.sqlite``: file creation, schema versioning, connections and transactions.

Rules (``docs/plans/phase-2.md`` section 3):

* The file is created ``0600`` (symlinks refused) inside a ``0700`` directory: it holds decrypted note text and is
  never backed up. A looser mode on an existing file or leaf directory is repaired (a directory we do not own and
  that is open to others is refused).
* One connection per operation, WAL journal, ``secure_delete`` on, schema version in ``PRAGMA user_version``.
* The index is disposable: a file that is really corrupt (``not a database``, ``malformed``, ``disk image``,
  failing ``quick_check``) or has a different schema version is deleted and recreated empty (the recorded revision
  is then ``None``, which makes the container reindex). There are no migrations. Busy/locked/I/O errors never
  trigger a recreation.
* Only a file that is clearly ours is ever deleted: zero-length, or an SQLite file whose header carries
  ``APPLICATION_ID``. Anything else (not SQLite, or a foreign SQLite database) raises ``IndexUnavailableError`` and is
  left untouched, not even ``chmod``-ed.
* Search and graph share the file, so recreating it empties **both** (every ``built_from()`` becomes ``None``):
  the bootstrap must check both revisions. Recreating is not coordinated with other open connections or processes,
  so a rolling deploy with different ``SCHEMA_VERSION`` values is unsupported: stop the old process first.
* ``secure_delete`` and the WAL checkpoints are best effort while another connection holds a read open.
"""

import contextlib
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from cerebro.domain.access import AccessCeiling
from cerebro.domain.enums import Sensitivity, Status
from cerebro.domain.errors import InvalidValueError

SCHEMA_VERSION: Final = 3
APPLICATION_ID: Final = 0x43524252  # "CRBR": marks an SQLite file as a Cérebro index (``PRAGMA application_id``)
_SQLITE_MAGIC: Final = b"SQLite format 3\x00"
_APPLICATION_ID_OFFSET: Final = 68
_BUSY_TIMEOUT_MS: Final = 5000

TIERS: Final = tuple(Sensitivity)
"""One FTS5 table per sensitivity tier (``notes_fts_0`` ...). bm25 statistics (document frequencies, average
length) are per table, so a query that reads only the tiers within its ceiling can never be influenced by, and so
never leak, text of a note above it."""


def fts_table(sensitivity: Sensitivity) -> str:
    return f"notes_fts_{TIERS.index(sensitivity)}"


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
    *(
        f"CREATE VIRTUAL TABLE {fts_table(tier)} USING fts5(title, summary, body, tokenize = 'porter unicode61')"
        for tier in TIERS
    ),
    """CREATE TABLE graph_nodes (
        note_id TEXT PRIMARY KEY,
        area TEXT NOT NULL,
        summary TEXT NOT NULL,
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

_CORRUPTION_MARKERS: Final = ("not a database", "malformed", "disk image", "vtable constructor failed")


class IndexUnavailableError(RuntimeError):
    """The SQLite index cannot be used (no FTS5 in this SQLite build, an unsafe path, ...)."""


class _NeedsRecreation(Exception):  # noqa: N818
    """The file is readable but unusable (stale schema version or failed integrity check)."""


def is_corruption(error: sqlite3.Error) -> bool:
    """Whether ``error`` means the file's content is damaged (as opposed to busy, locked, I/O or a bug)."""
    message = str(error).lower()
    return isinstance(error, sqlite3.DatabaseError) and any(marker in message for marker in _CORRUPTION_MARKERS)


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


def _prepare_directory(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.stat()
    if info.st_mode & 0o077 == 0:
        return
    if info.st_uid != os.geteuid():
        raise IndexUnavailableError("the index directory is not owned by this user and is open to others")
    directory.chmod(0o700)


def _require_ours(path: Path) -> None:
    """Raise unless ``path`` is absent, zero-length or an SQLite file carrying our ``application_id``.

    Read-only, from the raw header, so a foreign file is never opened by SQLite, modified or ``chmod``-ed.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return
    except OSError as error:
        raise IndexUnavailableError(f"cannot open the index file safely (symlinks are refused): {error}") from error
    try:
        header = os.read(descriptor, _APPLICATION_ID_OFFSET + 4)
    finally:
        os.close(descriptor)
    if not header:
        return
    if (
        len(header) < _APPLICATION_ID_OFFSET + 4
        or not header.startswith(_SQLITE_MAGIC)
        or int.from_bytes(header[_APPLICATION_ID_OFFSET:], "big") != APPLICATION_ID
    ):
        raise IndexUnavailableError(
            f"{path} exists but is not a Cérebro index file: refusing to touch it (move or delete it yourself)"
        )


def _create_private_file(path: Path) -> None:
    """Create ``path`` (empty) as ``0600`` if absent; repair a looser mode; refuse a symlink."""
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        raise IndexUnavailableError(f"cannot open the index file safely (symlinks are refused): {error}") from error
    try:
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _delete_files(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    # Autocommit: transactions are explicit (see ``transaction``). One connection per operation, so the default
    # same-thread check is right. ``secure_delete`` zeroes freed pages so removed text does not linger in the file.
    with contextlib.closing(sqlite3.connect(path, isolation_level=None, timeout=_BUSY_TIMEOUT_MS / 1000)) as connection:
        connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("PRAGMA synchronous = NORMAL")
        yield connection


def _enable_wal(connection: sqlite3.Connection) -> None:
    """Switch to WAL. The switch does not honour ``busy_timeout`` while another connection is doing the same, so
    retry for a while on ``locked``."""
    deadline = time.monotonic() + _BUSY_TIMEOUT_MS / 1000
    while True:
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as error:
            if ("locked" not in str(error) and "busy" not in str(error)) or time.monotonic() > deadline:
                raise
            time.sleep(0.01)


def _has_content(connection: sqlite3.Connection) -> bool:
    return bool(connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0) or bool(
        connection.execute("PRAGMA user_version").fetchone()[0]
    )


def _initialise(path: Path) -> None:
    with connect(path) as connection:
        # Claim an empty database first (in rollback-journal mode, so the marker is in the main file's header).
        with transaction(connection):
            claimed = connection.execute("PRAGMA application_id").fetchone()[0]
            if claimed == 0 and not _has_content(connection):
                connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            elif claimed != APPLICATION_ID:
                raise IndexUnavailableError("the index file is a foreign SQLite database: refusing to touch it")
        _enable_wal(connection)
        # Decided under the write lock, so two processes opening the same new file cannot both create the schema.
        with transaction(connection):
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            has_tables = _has_content(connection)
            if version == 0 and not has_tables:
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version != SCHEMA_VERSION:
                raise _NeedsRecreation
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise _NeedsRecreation


def prepare(path: Path) -> None:
    """Make ``path`` a usable, current-version index file (creating or recreating it as needed)."""
    _require_fts5()
    _prepare_directory(path.parent)
    _require_ours(path)
    _create_private_file(path)
    try:
        _initialise(path)
    except _NeedsRecreation:
        recreate(path)
    except sqlite3.DatabaseError as error:
        if not is_corruption(error):
            raise
        recreate(path)


def recreate(path: Path) -> None:
    """Throw the file away and start an empty current-version one (the recovery for a damaged index).

    Not coordinated with other connections or processes using the file. Refuses (``IndexUnavailableError``) a file
    that is not clearly ours.
    """
    _require_ours(path)
    _delete_files(path)
    _create_private_file(path)
    _initialise(path)


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


def checkpoint(connection: sqlite3.Connection) -> None:
    """Fold the WAL into the main file and truncate it, so deleted text does not linger in the ``-wal`` file.

    Best effort: while another connection holds a read open the checkpoint cannot truncate, and the text stays in the
    WAL until a later checkpoint."""
    with contextlib.suppress(sqlite3.OperationalError):  # busy (a reader is open): best effort
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


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
