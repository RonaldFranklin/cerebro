"""``SqliteSearchIndex``: ``SearchIndex`` on FTS5 (tokenizer ``porter unicode61``) in ``index.sqlite``."""

import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from cerebro.adapters.outbound.sqlite import _db
from cerebro.application.ports.search import SearchHit, SearchQuery
from cerebro.domain.access import AccessCeiling
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.note import Note, NoteId
from cerebro.domain.vocabulary import Area, NoteType

_REVISION_KEY: Final = "search_revision"
_WORD: Final = re.compile(r"\w+")
_MAX_MATCH_TERMS: Final = 64
_MAX_TERM_LENGTH: Final = 64
_SIMILAR_CANDIDATES: Final = 500
# Column weights of ``bm25``: title, summary, body. A title match outranks a body-only match.
_WEIGHTS: Final = "10.0, 5.0, 1.0"
_HIT_COLUMNS: Final = "n.note_id, n.title, n.summary, n.area, n.type, n.priority, n.sensitivity, n.status"


def _terms(text: str) -> list[str]:
    """Distinct casefolded word terms of ``text``, in order of appearance."""
    return list(dict.fromkeys(word.casefold() for word in _WORD.findall(text)))


def match_expression(text: str) -> str | None:
    """An FTS5 ``MATCH`` expression for ``text``, or ``None`` if it has no searchable term.

    User text never reaches ``MATCH`` as syntax: it is split into word terms (so quotes, ``*``, ``^``, ``-``, ``:``,
    parentheses and NUL disappear), each term is emitted as a double-quoted FTS5 string (so ``OR``, ``AND``,
    ``NOT`` and ``NEAR`` are plain words, and ``col:`` cannot select a column) and the terms are joined with ``OR``
    (any-term, as the port requires). Count and length are bounded.
    """
    terms = [t[:_MAX_TERM_LENGTH] for t in _terms(text)[:_MAX_MATCH_TERMS]]
    if not terms:
        return None
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def _hit(row: tuple[object, ...], score: float) -> SearchHit:
    note_id, title, summary, area, note_type, priority, sensitivity, status = row[:8]
    return SearchHit(
        id=NoteId(str(note_id)),
        title=str(title),
        summary=str(summary),
        area=Area(str(area)),
        type=NoteType(str(note_type)),
        priority=Priority(str(priority)),
        sensitivity=Sensitivity(str(sensitivity)),
        status=Status(str(status)),
        score=score,
    )


def _delete(connection: sqlite3.Connection, note_id: str) -> bool:
    """Drop a note's rows. Returns whether the note existed. The FTS segments keep the deleted terms until they are
    merged, so callers that replace or remove one note call ``_scrub`` afterwards."""
    existed = connection.execute("SELECT 1 FROM notes WHERE note_id = ?", (note_id,)).fetchone() is not None
    for tier in _db.TIERS:
        connection.execute(
            f"DELETE FROM {_db.fts_table(tier)} WHERE rowid = (SELECT id FROM notes WHERE note_id = ?)", (note_id,)
        )
    connection.execute("DELETE FROM notes WHERE note_id = ?", (note_id,))
    return existed


def _scrub(connection: sqlite3.Connection) -> None:
    """Merge every FTS segment so that the terms of deleted notes are physically gone (with ``secure_delete``)."""
    for tier in _db.TIERS:
        table = _db.fts_table(tier)
        connection.execute(f"INSERT INTO {table} ({table}) VALUES ('optimize')")


def _store(connection: sqlite3.Connection, note: Note) -> bool:
    """Index ``note``; returns whether it replaced an older version."""
    replaced = _delete(connection, note.id.value)
    terms = " ".join(sorted(_terms(f"{note.title} {note.summary} {note.body}")))
    cursor = connection.execute(
        "INSERT INTO notes (note_id, title, summary, area, type, priority, sensitivity, sens_rank, status, terms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            note.id.value,
            note.title,
            note.summary,
            note.area.value,
            note.type.value,
            note.priority.value,
            note.sensitivity.value,
            _db.sensitivity_rank(note.sensitivity),
            note.status.value,
            terms,
        ),
    )
    connection.execute(
        f"INSERT INTO {_db.fts_table(note.sensitivity)} (rowid, title, summary, body) VALUES (?, ?, ?, ?)",
        (cursor.lastrowid, note.title, note.summary, note.body),
    )
    return replaced


class SqliteSearchIndex:
    """Any-term, case-insensitive, stemmed full-text search; the ceiling is applied in SQL, so a hidden note is
    never read from the database into the process, let alone returned.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        _db.prepare(self._path)

    def upsert(self, note: Note) -> None:
        with _db.connect(self._path) as connection:
            with _db.transaction(connection):
                if _store(connection, note):
                    _scrub(connection)
            _db.checkpoint(connection)

    def remove(self, note_id: NoteId) -> None:
        with _db.connect(self._path) as connection:
            with _db.transaction(connection):
                if _delete(connection, note_id.value):
                    _scrub(connection)
            _db.checkpoint(connection)

    def rebuild(self, notes: Iterable[Note], *, revision: str | None = None) -> None:
        """Replace every row and the recorded revision in one transaction: if the work fails midway, the previous
        index (rows and revision) is untouched. This is also the recovery path for a stale or damaged index: if the
        file turns out to be corrupt, it is recreated empty and the rebuild runs once more.

        ``notes`` is read fully before the database is touched, so the retry can replay it.
        """
        items = list(notes)
        try:
            self._rebuild(items, revision)
        except sqlite3.DatabaseError as error:
            if not _db.is_corruption(error):
                raise
            _db.recreate(self._path)
            self._rebuild(items, revision)

    def _rebuild(self, notes: list[Note], revision: str | None) -> None:
        with _db.connect(self._path) as connection:
            with _db.transaction(connection):
                for tier in _db.TIERS:
                    connection.execute(f"DELETE FROM {_db.fts_table(tier)}")
                connection.execute("DELETE FROM notes")
                for note in notes:
                    _store(connection, note)
                _scrub(connection)
                _db.write_revision(connection, _REVISION_KEY, revision)
            _db.checkpoint(connection)

    def built_from(self) -> str | None:
        with _db.connect(self._path) as connection:
            return _db.read_revision(connection, _REVISION_KEY)

    def set_revision(self, revision: str | None) -> None:
        with _db.connect(self._path) as connection, _db.transaction(connection):
            _db.write_revision(connection, _REVISION_KEY, revision)

    @staticmethod
    def _tier_union(ceiling: AccessCeiling, columns: str, conditions: str) -> str:
        """``SELECT`` over the FTS tables of the tiers within ``ceiling`` only (``UNION ALL``).

        A tier above the ceiling is not read, so its rows cannot influence bm25 statistics. The bind parameters are
        ``[match, *conditions_params]`` once per tier.
        """
        selects = [
            f"SELECT {columns}, -bm25({table}, {_WEIGHTS}) AS score FROM {table} "
            f"JOIN notes n ON n.id = {table}.rowid WHERE {table} MATCH ? AND {conditions}"
            for table in (_db.fts_table(tier) for tier in _db.TIERS if tier <= ceiling.level)
        ]
        return " UNION ALL ".join(selects)

    def search(self, query: SearchQuery) -> tuple[SearchHit, ...]:
        """Matching visible notes, best first: score descending, then priority, then id.

        Scores are bm25 within each sensitivity tier (see ``_db.TIERS``), so scores of notes in different tiers are
        not strictly comparable; the order is still total and deterministic. Text with no searchable word (only
        punctuation) matches nothing; blank text lists notes by priority, then id.
        """
        where, params = _db.visibility("n", query.ceiling, include_history=query.include_history)
        conditions = [where]
        for column, values in (
            ("n.area", sorted(a.value for a in query.areas)),
            ("n.type", sorted(t.value for t in query.types)),
            ("n.priority", sorted(p.value for p in query.priorities)),
            ("n.status", sorted(s.value for s in query.statuses)),
        ):
            if values:
                conditions.append(f"{column} IN ({', '.join('?' * len(values))})")
                params.extend(values)
        expression = match_expression(query.text)
        if query.text.strip() and expression is None:
            return ()
        if expression is None:
            sql = (
                f"SELECT {_HIT_COLUMNS}, 0.0 FROM notes n WHERE {' AND '.join(conditions)} "
                "ORDER BY n.priority, n.note_id LIMIT ?"
            )
            arguments: list[str | int] = [*params, query.limit]
        else:
            union = self._tier_union(query.ceiling, _HIT_COLUMNS, " AND ".join(conditions))
            tiers = sum(1 for tier in _db.TIERS if tier <= query.ceiling.level)
            sql = f"SELECT * FROM ({union}) ORDER BY score DESC, priority, note_id LIMIT ?"
            arguments = [*([expression, *params] * tiers), query.limit]
        with _db.connect(self._path) as connection:
            rows = connection.execute(sql, arguments).fetchall()
        return tuple(_hit(row, float(row[8])) for row in rows)

    def find_similar(self, text: str, *, ceiling: AccessCeiling, limit: int = 5) -> tuple[SearchHit, ...]:
        """Current notes similar to ``text``, most similar first.

        Candidates come from FTS (stemmed, any-term over at most 64 distinct words of ``text``, best bm25 first,
        at most 500 candidates). Notes outside the candidates are never considered, so on a very large index a
        near-duplicate can in principle be missed. Candidates are then re-scored by the Jaccard similarity of their
        word sets, so the score is in (0, 1] and means "fraction of shared words" (callers threshold it).
        """
        wanted = frozenset(_terms(text))
        expression = match_expression(text)
        where, params = _db.visibility("n", ceiling, include_history=False)
        if expression is None or limit < 1:
            return ()
        union = self._tier_union(ceiling, f"{_HIT_COLUMNS}, n.terms", where)
        tiers = sum(1 for tier in _db.TIERS if tier <= ceiling.level)
        sql = f"SELECT * FROM ({union}) ORDER BY score DESC, priority, note_id LIMIT ?"
        with _db.connect(self._path) as connection:
            rows = connection.execute(sql, [*([expression, *params] * tiers), _SIMILAR_CANDIDATES]).fetchall()
        scored: list[SearchHit] = []
        for row in rows:
            have = frozenset(str(row[8]).split())
            score = len(wanted & have) / len(wanted | have)
            if score > 0.0:
                scored.append(_hit(row, score))
        scored.sort(key=lambda h: (-h.score, h.priority, h.id))
        return tuple(scored[:limit])
