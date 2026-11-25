"""``GraphIndex``: the link graph (backlinks, neighbors, orphans, broken links), filtered by ceiling."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from cerebro.domain.access import AccessCeiling
from cerebro.domain.links import Link
from cerebro.domain.note import NoteId
from cerebro.domain.note_ref import NoteRef


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphSlice:
    """Nodes (ordered by id) and the edges between them (ordered by source, target, kind, section)."""

    nodes: tuple[NoteRef, ...]
    edges: tuple[Link, ...]


class GraphIndex(Protocol):
    """Derived, rebuildable index. Every query takes the ceiling: hidden notes are neither nodes nor edge ends,
    and they never make a link look broken, so the answer cannot reveal that they exist.
    """

    def upsert(self, ref: NoteRef) -> None: ...

    def remove(self, note_id: NoteId) -> None:
        """Unknown ids are ignored."""
        ...

    def rebuild(self, refs: Iterable[NoteRef]) -> None:
        """Replace the whole graph atomically."""
        ...

    def graph(self, *, ceiling: AccessCeiling, include_history: bool = False) -> GraphSlice:
        """Every visible note and every edge between visible notes."""
        ...

    def backlinks(self, note_id: NoteId, *, ceiling: AccessCeiling, include_history: bool = False) -> tuple[Link, ...]:
        """Edges pointing at ``note_id`` from visible notes. Empty if ``note_id`` itself is not visible."""
        ...

    def neighbors(
        self, note_id: NoteId, *, hops: int, ceiling: AccessCeiling, include_history: bool = False
    ) -> GraphSlice:
        """The visible notes within ``hops`` (>= 1) links of ``note_id``, in either direction, and the edges
        between them. Paths through hidden notes do not count. Empty if ``note_id`` is not visible.
        """
        ...

    def orphans(self, *, ceiling: AccessCeiling, include_history: bool = False) -> tuple[NoteId, ...]:
        """Visible notes with no edge to or from another visible note, ordered by id."""
        ...

    def broken_links(self, *, ceiling: AccessCeiling, include_history: bool = False) -> tuple[Link, ...]:
        """Edges from visible notes to a note id that does not exist at all (hidden targets are not broken)."""
        ...
