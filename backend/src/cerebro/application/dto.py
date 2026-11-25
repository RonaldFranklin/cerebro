"""Requests and responses of the use cases.

Requests carry domain types (built by the inbound adapter). Responses are dedicated, body-free views unless a
note is read on purpose: they are never the ``Note`` entity, so a field added to ``Note`` cannot leak by accident.
"""

from dataclasses import dataclass
from datetime import date
from typing import Final

from cerebro.application.ports.graph import GraphSlice
from cerebro.application.ports.search import MAX_LIMIT, SearchHit
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.links import LinkKind
from cerebro.domain.note import Note, NoteId
from cerebro.domain.vocabulary import Area, NoteType

MAX_HOPS: Final = 5


def _require_bool(value: object, field: str) -> None:
    if type(value) is not bool:
        raise InvalidValueError(field, "expected a bool")


# --- shared views ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteSummary:
    """What search and the overview show about a note: never the body, never the sensitivity."""

    id: NoteId
    title: str
    type: NoteType
    area: Area
    priority: Priority
    status: Status
    summary: str

    @classmethod
    def from_hit(cls, hit: SearchHit) -> NoteSummary:
        return cls(
            id=hit.id,
            title=hit.title,
            type=hit.type,
            area=hit.area,
            priority=hit.priority,
            status=hit.status,
            summary=hit.summary,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class HubMember:
    """One entry of a hub's member list (ADR-0015): never the body."""

    id: NoteId
    title: str
    type: NoteType
    priority: Priority
    status: Status
    summary: str

    @classmethod
    def from_note(cls, note: Note) -> HubMember:
        return cls(
            id=note.id,
            title=note.title,
            type=note.type,
            priority=note.priority,
            status=note.status,
            summary=note.summary,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphNode:
    id: NoteId
    title: str
    type: NoteType
    priority: Priority
    status: Status


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphEdge:
    source: NoteId
    target: NoteId
    kind: LinkKind
    section: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphView:
    """Nodes and edges visible to the caller. ``center`` is set for a neighborhood, ``None`` for the full graph."""

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    center: NoteId | None = None

    @classmethod
    def from_slice(cls, graph: GraphSlice, *, center: NoteId | None = None) -> GraphView:
        nodes = tuple(
            GraphNode(id=n.id, title=n.title, type=n.type, priority=n.priority, status=n.status) for n in graph.nodes
        )
        ids = {n.id for n in nodes}
        # An edge is only shown when both ends are nodes of the view (defense in depth on top of the index).
        edges = tuple(
            GraphEdge(source=e.source, target=e.target, kind=e.kind, section=e.section)
            for e in graph.edges
            if e.source in ids and e.target in ids
        )
        return cls(nodes=nodes, edges=edges, center=center)


# --- GetOverview ----------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class GetOverviewRequest:
    """No parameters: the overview is the same shape for every agent (history is never part of it)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CoreNote:
    """A P0 note served in full."""

    id: NoteId
    title: str
    summary: str
    body: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Overview:
    """First call of every agent: the P0 notes (within the core budget), the areas and the hubs to navigate by.

    ``core_truncated`` is true when visible P0 notes did not all fit in the core word budget; the rest are omitted.
    """

    core: tuple[CoreNote, ...]
    core_words: int
    core_truncated: bool
    areas: tuple[Area, ...]
    hubs: tuple[NoteSummary, ...]


# --- SearchNotes ----------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchNotesRequest:
    text: str = ""
    areas: frozenset[Area] = frozenset()
    types: frozenset[NoteType] = frozenset()
    priorities: frozenset[Priority] = frozenset()
    statuses: frozenset[Status] = frozenset()
    include_history: bool = False
    limit: int = 20

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise InvalidValueError("text", "expected a string")
        _require_bool(self.include_history, "include_history")
        if type(self.limit) is not int or not 1 <= self.limit <= MAX_LIMIT:
            raise InvalidValueError("limit", f"expected an integer from 1 to {MAX_LIMIT}")


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchResult:
    note: NoteSummary
    score: float


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchResults:
    """Matches the caller may see, best first. There is deliberately no total count: it could include hidden notes."""

    results: tuple[SearchResult, ...]


# --- ReadNote -------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadNoteRequest:
    note_id: NoteId
    section: str | None = None
    include_history: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.note_id, NoteId):
            raise InvalidValueError("note_id", "expected a NoteId")
        if self.section is not None and not (isinstance(self.section, str) and self.section.strip()):
            raise InvalidValueError("section", "expected a non-empty string or None")
        _require_bool(self.include_history, "include_history")

    @classmethod
    def parse(cls, reference: str, *, include_history: bool = False) -> ReadNoteRequest:
        """Build a request from ``id``, ``id#Section`` or ``[[id#Section]]``."""
        if not isinstance(reference, str):
            raise InvalidValueError("reference", "expected a string")
        text = reference.strip()
        if text.startswith("[[") and text.endswith("]]"):
            text = text[2:-2]
        target, _, section = text.partition("#")
        return cls(note_id=NoteId(target.strip()), section=section.strip() or None, include_history=include_history)


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteView:
    """A note read on purpose. ``body`` is the whole body or, when a section was asked for, that section.

    ``members`` is ``None`` for ordinary notes and, for a hub, the members the caller may see (ADR-0015).
    ``content_hash`` is of the whole stored note, whichever part of the body is returned.
    """

    id: NoteId
    title: str
    type: NoteType
    area: Area
    priority: Priority
    sensitivity: Sensitivity
    status: Status
    summary: str
    hub: NoteId | None
    related: tuple[NoteId, ...]
    superseded_by: NoteId | None
    lang: str
    created: date
    updated: date
    review_by: date | None
    content_hash: str
    section: str | None
    body: str
    members: tuple[HubMember, ...] | None = None

    @classmethod
    def from_note(
        cls, note: Note, *, body: str, section: str | None = None, members: tuple[HubMember, ...] | None = None
    ) -> NoteView:
        return cls(
            id=note.id,
            title=note.title,
            type=note.type,
            area=note.area,
            priority=note.priority,
            sensitivity=note.sensitivity,
            status=note.status,
            summary=note.summary,
            hub=note.hub,
            related=note.related,
            superseded_by=note.superseded_by,
            lang=note.lang,
            created=note.created,
            updated=note.updated,
            review_by=note.review_by,
            content_hash=content_hash(note),
            section=section,
            body=body,
            members=members,
        )


# --- GetNeighbors / GetGraph ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class GetNeighborsRequest:
    note_id: NoteId
    hops: int = 1
    include_history: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.note_id, NoteId):
            raise InvalidValueError("note_id", "expected a NoteId")
        if type(self.hops) is not int or not 1 <= self.hops <= MAX_HOPS:
            raise InvalidValueError("hops", f"expected an integer from 1 to {MAX_HOPS}")
        _require_bool(self.include_history, "include_history")


@dataclass(frozen=True, slots=True, kw_only=True)
class GetGraphRequest:
    include_history: bool = False

    def __post_init__(self) -> None:
        _require_bool(self.include_history, "include_history")
