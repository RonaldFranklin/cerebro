"""Security and robustness of ``index.sqlite``: score isolation per tier, corruption, secure delete, paths."""

import os
import sqlite3
import stat
import threading
from contextlib import closing
from pathlib import Path

import pytest

from cerebro.adapters.outbound.sqlite import IndexUnavailableError, SqliteGraphIndex, SqliteSearchIndex, _db
from cerebro.application.ports import SearchQuery
from cerebro.domain.enums import Sensitivity
from cerebro.domain.note import NoteId
from tests.contract.search_contract import PUBLIC, RESTRICTED, ids, note, ref


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "index.sqlite"


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_scores_do_not_depend_on_notes_above_the_ceiling(path: Path) -> None:
    index = SqliteSearchIndex(path)
    index.upsert(note("open-note", title="Kubernetes cluster", body="Plain.\n", sensitivity=Sensitivity.PUBLIC))
    index.upsert(note("other-note", title="Unrelated", body="Plain.\n", sensitivity=Sensitivity.PUBLIC))
    query = SearchQuery(ceiling=PUBLIC, text="kubernetes")
    before = index.search(query)
    similar_before = index.find_similar("Kubernetes cluster", ceiling=PUBLIC)
    assert before
    assert similar_before
    for i in range(5):
        index.upsert(
            note(
                f"hidden-{i}-note",
                title="Kubernetes kubernetes",
                body="kubernetes " * 20,
                sensitivity=Sensitivity.RESTRICTED,
            )
        )
        index.upsert(note(f"inner-{i}-note", title="Kubernetes", sensitivity=Sensitivity.INTERNAL))
    assert index.search(query) == before
    assert index.find_similar("Kubernetes cluster", ceiling=PUBLIC) == similar_before


def test_two_processes_preparing_the_same_new_file(path: Path) -> None:
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def open_index() -> None:
        try:
            barrier.wait()
            SqliteSearchIndex(path).upsert(note("a-note"))
            SqliteGraphIndex(path).upsert(ref("a-note"))
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=open_index) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert ids(SqliteSearchIndex(path).search(SearchQuery(ceiling=RESTRICTED))) == ["a-note"]


@pytest.mark.parametrize(
    ("error", "corrupt"),
    [
        (sqlite3.DatabaseError("file is not a database"), True),
        (sqlite3.DatabaseError("database disk image is malformed"), True),
        (sqlite3.OperationalError("database is locked"), False),
        (sqlite3.OperationalError("disk I/O error"), False),
        (sqlite3.OperationalError("table notes already exists"), False),
        (sqlite3.OperationalError("unable to open database file"), False),
    ],
)
def test_only_real_corruption_is_recreated(error: sqlite3.Error, corrupt: bool) -> None:
    assert _db.is_corruption(error) is corrupt


def _scribble_over_data_pages(path: Path) -> None:
    size = path.stat().st_size
    with path.open("r+b") as handle:
        handle.seek(4096)
        handle.write(b"\xde\xad\xbe\xef" * ((size - 4096) // 4))


def test_a_corrupted_data_page_is_healed_on_open(path: Path) -> None:
    SqliteSearchIndex(path).rebuild([note(f"n{i:03d}-note", body="word " * 200) for i in range(30)], revision="r")
    _scribble_over_data_pages(path)
    index = SqliteSearchIndex(path)
    assert index.built_from() is None
    assert index.search(SearchQuery(ceiling=RESTRICTED)) == ()
    index.rebuild([note("fresh-note")], revision="r2")
    assert ids(index.search(SearchQuery(ceiling=RESTRICTED))) == ["fresh-note"]


def test_rebuild_heals_a_file_that_got_corrupted_while_running(path: Path) -> None:
    index = SqliteSearchIndex(path)
    index.rebuild([note(f"n{i:03d}-note", body="word " * 200) for i in range(30)], revision="r")
    _scribble_over_data_pages(path)
    index.rebuild([note("fresh-note")], revision="r2")
    assert ids(index.search(SearchQuery(ceiling=RESTRICTED))) == ["fresh-note"]
    assert index.built_from() == "r2"
    assert mode(path) == 0o600


def test_a_failing_transaction_rolls_back_rows_and_revision(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index = SqliteSearchIndex(path)
    index.rebuild([note("old-note")], revision="rev-old")

    def explode(*_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(_db, "write_revision", explode)
    with pytest.raises(RuntimeError):
        index.rebuild([note("new-note")], revision="rev-new")
    monkeypatch.undo()
    assert ids(index.search(SearchQuery(ceiling=RESTRICTED))) == ["old-note"]
    assert index.built_from() == "rev-old"


def test_removed_text_does_not_linger_in_the_file(path: Path) -> None:
    index = SqliteSearchIndex(path)
    secret = "quuxalphabetical"
    index.upsert(note("gone-note", title=f"Title {secret}", summary=f"Sum {secret}", body=f"Body {secret}\n"))
    index.upsert(note("stay-note"))
    index.remove(NoteId("gone-note"))
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for candidate in (path, Path(f"{path}-wal")):
        if candidate.exists():
            content = candidate.read_bytes()
            assert secret.encode() not in content
            assert b"quuxalphabet" not in content


def test_a_symlink_is_refused(path: Path, tmp_path: Path) -> None:
    path.parent.mkdir(parents=True)
    target = tmp_path / "elsewhere.sqlite"
    target.touch()
    path.symlink_to(target)
    with pytest.raises(IndexUnavailableError, match="symlink"):
        SqliteSearchIndex(path)


def test_an_existing_leaf_directory_is_tightened(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.parent.chmod(0o755)
    SqliteSearchIndex(path)
    assert mode(path.parent) == 0o700


def test_an_open_directory_owned_by_someone_else_is_refused(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path.parent.mkdir(parents=True)
    path.parent.chmod(0o755)
    owner = path.parent.stat().st_uid
    monkeypatch.setattr(os, "geteuid", lambda: owner + 1)
    with pytest.raises(IndexUnavailableError, match="not owned"):
        SqliteSearchIndex(path)


def test_a_non_sqlite_file_is_refused_and_left_untouched(path: Path) -> None:
    path.parent.mkdir(parents=True)
    content = b"this is somebody's notes, not a database" * 50
    path.write_bytes(content)
    path.chmod(0o644)
    with pytest.raises(IndexUnavailableError, match="not a Cérebro index"):
        SqliteSearchIndex(path)
    assert path.read_bytes() == content
    assert mode(path) == 0o644


@pytest.mark.parametrize("user_version", [0, 3])
def test_a_foreign_sqlite_database_is_refused_and_left_untouched(path: Path, user_version: int) -> None:
    path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE precious (x TEXT)")
        connection.execute("INSERT INTO precious VALUES ('keep me')")
        connection.execute(f"PRAGMA user_version = {user_version}")
    path.chmod(0o644)
    before = path.read_bytes()
    with pytest.raises(IndexUnavailableError):
        SqliteSearchIndex(path)
    with pytest.raises(IndexUnavailableError):
        SqliteGraphIndex(path)
    assert path.read_bytes() == before
    assert mode(path) == 0o644


def test_an_empty_file_we_created_is_adopted(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.touch()
    assert SqliteSearchIndex(path).built_from() is None


def test_recreating_after_corruption_empties_both_indexes(path: Path) -> None:
    SqliteSearchIndex(path).rebuild([note(f"n{i:03d}-note", body="word " * 200) for i in range(30)], revision="rs")
    SqliteGraphIndex(path).rebuild([ref("a-note")], revision="rg")
    _scribble_over_data_pages(path)
    search = SqliteSearchIndex(path)
    graph = SqliteGraphIndex(path)
    assert search.built_from() is None
    assert graph.built_from() is None
    assert graph.graph(ceiling=RESTRICTED).nodes == ()
