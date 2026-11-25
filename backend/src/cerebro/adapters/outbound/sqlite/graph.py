"""``SqliteGraphIndex``: ``GraphIndex`` over ``graph_nodes`` / ``graph_edges`` in ``index.sqlite``."""

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from cerebro.adapters.outbound.sqlite import _db
from cerebro.application.ports.graph import GraphSlice
from cerebro.domain.access import AccessCeiling
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.links import Link, LinkKind, WikiLink
from cerebro.domain.note import NoteId
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.vocabulary import NoteType

_REVISION_KEY: Final = "graph_revision"
_NODE_COLUMNS: Final = (
    "note_id, title, summary, type, priority, sensitivity, status, hub, superseded_by, "
    "related, body_links, loose_body_targets"
)
_EDGE_ORDER: Final = "ORDER BY e.source, e.target, e.kind, e.section"


def _ref(row: tuple[object, ...]) -> NoteRef:
    (
        note_id,
        title,
        summary,
        note_type,
        priority,
        sensitivity,
        status,
        hub,
        superseded_by,
        related,
        body_links,
        loose,
    ) = row
    return NoteRef(
        id=NoteId(str(note_id)),
        title=str(title),
        summary=str(summary),
        type=NoteType(str(note_type)),
        priority=Priority(str(priority)),
        sensitivity=Sensitivity(str(sensitivity)),
        status=Status(str(status)),
        hub=None if hub is None else NoteId(str(hub)),
        related=tuple(NoteId(i) for i in json.loads(str(related))),
        superseded_by=None if superseded_by is None else NoteId(str(superseded_by)),
        body_links=tuple(
            WikiLink(target=NoteId(target), section=section) for target, section in json.loads(str(body_links))
        ),
        loose_body_targets=tuple(NoteId(i) for i in json.loads(str(loose))),
    )


def _link(row: tuple[object, ...]) -> Link:
    source, target, kind, section = row
    return Link(
        source=NoteId(str(source)), target=NoteId(str(target)), kind=LinkKind(str(kind)), section=str(section) or None
    )


def _delete(connection: sqlite3.Connection, note_id: str) -> None:
    connection.execute("DELETE FROM graph_nodes WHERE note_id = ?", (note_id,))
    connection.execute("DELETE FROM graph_edges WHERE source = ?", (note_id,))


def _store(connection: sqlite3.Connection, ref: NoteRef) -> None:
    _delete(connection, ref.id.value)
    connection.execute(
        f"INSERT INTO graph_nodes ({_NODE_COLUMNS}, sens_rank) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ref.id.value,
            ref.title,
            ref.summary,
            ref.type.value,
            ref.priority.value,
            ref.sensitivity.value,
            ref.status.value,
            None if ref.hub is None else ref.hub.value,
            None if ref.superseded_by is None else ref.superseded_by.value,
            json.dumps([i.value for i in ref.related]),
            json.dumps([[b.target.value, b.section] for b in ref.body_links]),
            json.dumps([i.value for i in ref.loose_body_targets]),
            _db.sensitivity_rank(ref.sensitivity),
        ),
    )
    connection.executemany(
        "INSERT INTO graph_edges (source, target, kind, section) VALUES (?, ?, ?, ?)",
        [(link.source.value, link.target.value, link.kind.value, link.section or "") for link in ref.links],
    )


class SqliteGraphIndex:
    """Edges are stored per source note; visibility is applied in SQL to both ends of every edge, so a hidden note
    is never a node, an edge end, a hop on a path or a broken link."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        _db.prepare(self._path)

    def upsert(self, ref: NoteRef) -> None:
        with _db.connect(self._path) as connection:
            with _db.transaction(connection):
                _store(connection, ref)
            _db.checkpoint(connection)

    def remove(self, note_id: NoteId) -> None:
        with _db.connect(self._path) as connection:
            with _db.transaction(connection):
                _delete(connection, note_id.value)
            _db.checkpoint(connection)

    def rebuild(self, refs: Iterable[NoteRef], *, revision: str | None = None) -> None:
        """Rows and revision are replaced in one transaction (see ``SqliteSearchIndex.rebuild``, including the
        recreate-and-retry on a corrupt file)."""
        items = list(refs)
        try:
            self._rebuild(items, revision)
        except sqlite3.DatabaseError as error:
            if not _db.is_corruption(error):
                raise
            _db.recreate(self._path)
            self._rebuild(items, revision)

    def _rebuild(self, refs: list[NoteRef], revision: str | None) -> None:
        with _db.connect(self._path) as connection:
            with _db.transaction(connection):
                connection.execute("DELETE FROM graph_nodes")
                connection.execute("DELETE FROM graph_edges")
                for ref in refs:
                    _store(connection, ref)
                _db.write_revision(connection, _REVISION_KEY, revision)
            _db.checkpoint(connection)

    def built_from(self) -> str | None:
        with _db.connect(self._path) as connection:
            return _db.read_revision(connection, _REVISION_KEY)

    def set_revision(self, revision: str | None) -> None:
        with _db.connect(self._path) as connection, _db.transaction(connection):
            _db.write_revision(connection, _REVISION_KEY, revision)

    # -- queries ---------------------------------------------------------------------------------------------

    @staticmethod
    def _nodes(
        connection: sqlite3.Connection, ceiling: AccessCeiling, include_history: bool, ids: set[str] | None = None
    ) -> list[NoteRef]:
        where, params = _db.visibility("n", ceiling, include_history=include_history)
        rows = connection.execute(
            f"SELECT {', '.join('n.' + c.strip() for c in _NODE_COLUMNS.split(','))} FROM graph_nodes n "
            f"WHERE {where} ORDER BY n.note_id",
            params,
        ).fetchall()
        refs = [_ref(row) for row in rows]
        return refs if ids is None else [r for r in refs if r.id.value in ids]

    @staticmethod
    def _edges(connection: sqlite3.Connection, ceiling: AccessCeiling, include_history: bool) -> list[Link]:
        """Every edge whose two ends are both visible."""
        source_where, source_params = _db.visibility("s", ceiling, include_history=include_history)
        target_where, target_params = _db.visibility("t", ceiling, include_history=include_history)
        rows = connection.execute(
            "SELECT e.source, e.target, e.kind, e.section FROM graph_edges e "
            "JOIN graph_nodes s ON s.note_id = e.source JOIN graph_nodes t ON t.note_id = e.target "
            f"WHERE {source_where} AND {target_where} {_EDGE_ORDER}",
            [*source_params, *target_params],
        ).fetchall()
        return [_link(row) for row in rows]

    def graph(self, *, ceiling: AccessCeiling, include_history: bool = False) -> GraphSlice:
        with _db.connect(self._path) as connection:
            nodes = self._nodes(connection, ceiling, include_history)
            edges = self._edges(connection, ceiling, include_history)
        return GraphSlice(nodes=tuple(nodes), edges=tuple(edges))

    def backlinks(self, note_id: NoteId, *, ceiling: AccessCeiling, include_history: bool = False) -> tuple[Link, ...]:
        with _db.connect(self._path) as connection:
            nodes = self._nodes(connection, ceiling, include_history)
            if note_id not in {n.id for n in nodes}:
                return ()
            edges = self._edges(connection, ceiling, include_history)
        return tuple(e for e in edges if e.target == note_id)

    def neighbors(
        self, note_id: NoteId, *, hops: int, ceiling: AccessCeiling, include_history: bool = False
    ) -> GraphSlice:
        if type(hops) is not int or hops < 1:
            raise InvalidValueError("hops", "expected an integer >= 1")
        with _db.connect(self._path) as connection:
            nodes = {n.id: n for n in self._nodes(connection, ceiling, include_history)}
            if note_id not in nodes:
                return GraphSlice(nodes=(), edges=())
            edges = self._edges(connection, ceiling, include_history)
        adjacent: dict[NoteId, set[NoteId]] = {i: set() for i in nodes}
        for edge in edges:
            adjacent[edge.source].add(edge.target)
            adjacent[edge.target].add(edge.source)
        seen = {note_id}
        frontier = {note_id}
        for _ in range(hops):  # bounded by the frontier: it empties once the component is exhausted
            frontier = {n for current in frontier for n in adjacent[current]} - seen
            if not frontier:
                break
            seen |= frontier
        return GraphSlice(
            nodes=tuple(nodes[i] for i in sorted(seen)),
            edges=tuple(e for e in edges if e.source in seen and e.target in seen),
        )

    def orphans(self, *, ceiling: AccessCeiling, include_history: bool = False) -> tuple[NoteId, ...]:
        with _db.connect(self._path) as connection:
            nodes = self._nodes(connection, ceiling, include_history)
            edges = self._edges(connection, ceiling, include_history)
        connected = {end for e in edges for end in (e.source, e.target)}
        return tuple(n.id for n in nodes if n.id not in connected)

    def broken_links(self, *, ceiling: AccessCeiling, include_history: bool = False) -> tuple[Link, ...]:
        where, params = _db.visibility("s", ceiling, include_history=include_history)
        with _db.connect(self._path) as connection:
            rows = connection.execute(
                "SELECT e.source, e.target, e.kind, e.section FROM graph_edges e "
                "JOIN graph_nodes s ON s.note_id = e.source "
                "LEFT JOIN graph_nodes t ON t.note_id = e.target "
                f"WHERE t.note_id IS NULL AND {where} {_EDGE_ORDER}",
                params,
            ).fetchall()
        return tuple(_link(row) for row in rows)
