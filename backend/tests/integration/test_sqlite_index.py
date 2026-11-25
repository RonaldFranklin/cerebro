"""Behaviour of ``index.sqlite`` that the shared contracts cannot see."""

import sqlite3
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from cerebro.adapters.outbound.sqlite import IndexUnavailableError, SqliteGraphIndex, SqliteSearchIndex, _db
from cerebro.adapters.outbound.sqlite.search import match_expression
from cerebro.application.ports import SearchQuery
from cerebro.domain.access import AccessCeiling
from cerebro.domain.enums import Sensitivity
from cerebro.domain.note import Note, NoteId
from tests.contract.search_contract import INTERNAL, PUBLIC, RESTRICTED, ids, note, ref


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "index.sqlite"


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_file_is_private_and_directory_is_0700(path: Path) -> None:
    SqliteSearchIndex(path).upsert(note("a-note"))
    assert mode(path) == 0o600
    assert mode(path.parent) == 0o700
    # Sidecars exist only while a connection is open: hold one.
    holder = sqlite3.connect(path)
    try:
        holder.execute("SELECT count(*) FROM notes").fetchall()
        holder.execute("BEGIN")
        holder.execute("SELECT count(*) FROM notes").fetchall()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{path}{suffix}")
            assert sidecar.exists()
            assert mode(sidecar) == 0o600
    finally:
        holder.close()


def test_a_loose_existing_file_is_repaired(path: Path) -> None:
    SqliteSearchIndex(path)
    path.chmod(0o644)
    SqliteSearchIndex(path)
    assert mode(path) == 0o600


def test_uses_wal(path: Path) -> None:
    SqliteSearchIndex(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_data_and_revisions_persist_across_reopen(path: Path) -> None:
    SqliteSearchIndex(path).rebuild([note("kept-note", title="Persisted")], revision="rev-1")
    SqliteGraphIndex(path).rebuild([ref("kept-note")], revision="rev-2")
    search, graph = SqliteSearchIndex(path), SqliteGraphIndex(path)
    assert ids(search.search(SearchQuery(ceiling=RESTRICTED, text="persisted"))) == ["kept-note"]
    assert search.built_from() == "rev-1"
    assert graph.built_from() == "rev-2"
    assert [n.id.value for n in graph.graph(ceiling=RESTRICTED).nodes] == ["kept-note"]


def test_search_and_graph_revisions_are_independent(path: Path) -> None:
    search, graph = SqliteSearchIndex(path), SqliteGraphIndex(path)
    search.set_revision("rev-s")
    assert graph.built_from() is None


def test_porter_stemming_and_accents(path: Path) -> None:
    index = SqliteSearchIndex(path)
    index.upsert(note("conf-note", title="Server configuration", body="Nothing.\n"))
    index.upsert(note("pt-note", title="Configuração do café", body="Nada.\n"))
    assert ids(index.search(SearchQuery(ceiling=RESTRICTED, text="configured"))) == ["conf-note"]
    assert ids(index.search(SearchQuery(ceiling=RESTRICTED, text="configuracao cafe"))) == ["pt-note"]
    assert ids(index.search(SearchQuery(ceiling=RESTRICTED, text="CAFÉ"))) == ["pt-note"]


def test_rebuild_is_atomic_when_the_source_fails_midway(path: Path) -> None:
    index = SqliteSearchIndex(path)
    index.rebuild([note("old-note", title="Old")], revision="rev-old")

    def failing() -> Iterator[Note]:
        yield note("new-note", title="New")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        index.rebuild(failing(), revision="rev-new")
    assert ids(index.search(SearchQuery(ceiling=RESTRICTED))) == ["old-note"]
    assert ids(index.search(SearchQuery(ceiling=RESTRICTED, text="old"))) == ["old-note"]
    assert index.built_from() == "rev-old"


def test_graph_rebuild_is_atomic_when_the_source_fails_midway(path: Path) -> None:
    graph = SqliteGraphIndex(path)
    graph.rebuild([ref("old-note")], revision="rev-old")

    def failing() -> Iterator[object]:
        yield ref("new-note")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        graph.rebuild(failing(), revision="rev-new")  # type: ignore[arg-type]
    assert [n.id.value for n in graph.graph(ceiling=RESTRICTED).nodes] == ["old-note"]
    assert graph.built_from() == "rev-old"


def test_graph_round_trips_the_note_ref(path: Path) -> None:
    graph = SqliteGraphIndex(path)
    original = ref("a-note", body="See [[b-note#Part one]] and [[c-note]].\n")
    graph.upsert(original)
    (restored,) = graph.graph(ceiling=RESTRICTED).nodes
    assert restored == original


def test_a_different_schema_version_is_recreated_empty(path: Path) -> None:
    SqliteSearchIndex(path).rebuild([note("a-note")], revision="rev-1")
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {_db.SCHEMA_VERSION + 1}")
    index = SqliteSearchIndex(path)
    assert index.built_from() is None
    assert index.search(SearchQuery(ceiling=RESTRICTED)) == ()


def test_missing_fts5_fails_clearly(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = sqlite3.connect

    class NoFts(sqlite3.Connection):
        def execute(self, sql: str, *args: object) -> sqlite3.Cursor:
            if "fts5" in sql:
                raise sqlite3.OperationalError("no such module: fts5")
            return super().execute(sql, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: real(*a, factory=NoFts, **k))
    with pytest.raises(IndexUnavailableError, match="FTS5"):
        SqliteSearchIndex(path)


INJECTION_CORPUS = [
    '"',
    '""',
    '"unbalanced',
    'unbalanced"',
    "OR",
    "AND NOT",
    "NEAR(",
    "NEAR(a b, 2)",
    "title:secret",
    "body: x",
    "{title body}: x",
    "*",
    "a*",
    "-secret",
    "^title",
    "(",
    ")",
    "a OR",
    "x" * 100_000,
    "word " * 10_000,
    "nul\x00byte",
    "\x00",
    "'; DROP TABLE notes; --",
    "!!! ??? ...",
]


@pytest.mark.parametrize("text", INJECTION_CORPUS)
def test_hostile_query_text_never_raises_or_leaks(path: Path, text: str) -> None:
    index = SqliteSearchIndex(path)
    index.upsert(note("open-note", title="Secret open", sensitivity=Sensitivity.PUBLIC))
    index.upsert(note("hidden-note", title="Secret hidden", sensitivity=Sensitivity.RESTRICTED))
    hits = index.search(SearchQuery(ceiling=PUBLIC, text=text))
    assert "hidden-note" not in ids(hits)
    index.find_similar(text, ceiling=PUBLIC)
    assert ids(index.search(SearchQuery(ceiling=RESTRICTED))) == ["hidden-note", "open-note"]


def test_operators_are_plain_words(path: Path) -> None:
    index = SqliteSearchIndex(path)
    index.upsert(note("a-note", title="alpha", body="beta.\n"))
    index.upsert(note("b-note", title="gamma", body="delta.\n"))
    assert ids(index.search(SearchQuery(ceiling=RESTRICTED, text="alpha OR gamma"))) == ["a-note", "b-note"]
    assert ids(index.search(SearchQuery(ceiling=RESTRICTED, text="title:gamma"))) == ["b-note"]
    assert ids(index.search(SearchQuery(ceiling=RESTRICTED, text="NOT alpha"))) == ["a-note"]
    assert index.search(SearchQuery(ceiling=RESTRICTED, text="alp*")) == ()


def test_match_expression_quotes_every_term() -> None:
    assert match_expression("a OR b") == '"a" OR "or" OR "b"'
    assert match_expression('x"y') == '"x" OR "y"'
    assert match_expression("!!!") is None
    assert match_expression("") is None


def test_no_restricted_note_for_an_internal_ceiling_even_on_an_exact_match(path: Path) -> None:
    index = SqliteSearchIndex(path)
    index.upsert(
        note(
            "vault-note",
            title="Vault master recovery phrase",
            summary="Where the recovery phrase is kept.",
            body="The recovery phrase is zebra-quartz-meadow.\n",
            sensitivity=Sensitivity.RESTRICTED,
        )
    )
    for text in ("Vault master recovery phrase", "zebra-quartz-meadow", "vault", "recovery"):
        assert index.search(SearchQuery(ceiling=INTERNAL, text=text)) == ()
        assert index.find_similar(text, ceiling=INTERNAL) == ()
    assert index.search(SearchQuery(ceiling=INTERNAL, include_history=True)) == ()
    assert len(index.search(SearchQuery(ceiling=AccessCeiling(Sensitivity.RESTRICTED), text="vault"))) == 1


def test_find_similar_score_is_a_word_overlap_fraction(path: Path) -> None:
    index = SqliteSearchIndex(path)
    index.upsert(note("same-note", title="Backup plan", summary="", body=""))
    (hit,) = index.find_similar("Backup plan", ceiling=RESTRICTED)
    assert hit.score == 1.0


def test_graph_hidden_notes_do_not_leak_through_the_adapter(path: Path) -> None:
    graph = SqliteGraphIndex(path)
    graph.rebuild(
        [
            ref("start-note", sensitivity=Sensitivity.PUBLIC, body="[[secret-note]]\n"),
            ref("secret-note", sensitivity=Sensitivity.RESTRICTED, body="[[start-note]]\n"),
        ]
    )
    assert graph.backlinks(NoteId("start-note"), ceiling=PUBLIC) == ()
    assert graph.broken_links(ceiling=PUBLIC) == ()
    assert graph.orphans(ceiling=PUBLIC) == (NoteId("start-note"),)


def test_neighbors_with_a_huge_hop_count_stops_at_the_frontier(path: Path) -> None:
    graph = SqliteGraphIndex(path)
    graph.rebuild([ref("a-note", body="[[b-note]]\n"), ref("b-note")])
    result = graph.neighbors(NoteId("a-note"), hops=10**9, ceiling=RESTRICTED)
    assert [n.id.value for n in result.nodes] == ["a-note", "b-note"]
