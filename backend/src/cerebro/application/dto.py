"""Requests and responses of the use cases.

Requests carry domain types (built by the inbound adapter). Responses are dedicated, body-free views unless a
note is read on purpose: they are never the ``Note`` entity, so a field added to ``Note`` cannot leak by accident.
"""

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Final

from cerebro.application.ports.graph import GraphSlice
from cerebro.application.ports.search import MAX_LIMIT, SearchHit
from cerebro.domain.access import AccessCeiling, Scope, is_visible, validate_agent_name
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.links import LinkKind
from cerebro.domain.note import Note, NoteId
from cerebro.domain.report import Severity, ValidationReport, ViolationCode
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
    summary: str
    type: NoteType
    area: Area
    sensitivity: Sensitivity
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
    def from_slice(
        cls, graph: GraphSlice, *, ceiling: AccessCeiling, include_history: bool, center: NoteId | None = None
    ) -> GraphView:
        # Nodes are re-checked against the ceiling here too (defense in depth on top of the index).
        nodes = tuple(
            GraphNode(
                id=n.id,
                title=n.title,
                summary=n.summary,
                type=n.type,
                area=n.area,
                priority=n.priority,
                sensitivity=n.sensitivity,
                status=n.status,
            )
            for n in graph.nodes
            if is_visible(n.sensitivity, n.status, ceiling, include_history=include_history)
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
    ``base_hash`` is available to authenticated writers only on a complete, unredacted read.
    ``hub``, ``related`` and ``superseded_by`` list only references whose target the caller may see; the others are
    dropped, and wikilinks in ``body`` to notes the caller may not see are replaced by the redaction marker.
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
    visible_content_hash: str | None = None
    base_hash: str | None = None

    @classmethod
    def from_note(
        cls,
        note: Note,
        *,
        body: str,
        hub: NoteId | None,
        related: tuple[NoteId, ...],
        superseded_by: NoteId | None,
        section: str | None = None,
        members: tuple[HubMember, ...] | None = None,
        visible_title: str | None = None,
        visible_summary: str | None = None,
        authenticated_writer: bool = False,
    ) -> NoteView:
        stored_hash = content_hash(note)
        title = note.title if visible_title is None else visible_title
        summary = note.summary if visible_summary is None else visible_summary
        base_hash = None
        visible_hash = None
        if section is None:
            visible = replace(
                note, title=title, summary=summary, body=body, hub=hub, related=related, superseded_by=superseded_by
            )
            if visible == note:
                visible_hash = stored_hash
                if authenticated_writer:
                    base_hash = stored_hash
            else:
                visible_hash = content_hash(visible)
        return cls(
            id=note.id,
            title=title,
            type=note.type,
            area=note.area,
            priority=note.priority,
            sensitivity=note.sensitivity,
            status=note.status,
            summary=summary,
            hub=hub,
            related=related,
            superseded_by=superseded_by,
            lang=note.lang,
            created=note.created,
            updated=note.updated,
            review_by=note.review_by,
            content_hash=stored_hash,
            visible_content_hash=visible_hash,
            base_hash=base_hash,
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


class ApplyWarningCode(StrEnum):
    PREVIOUS_VERSION_IN_HISTORY = "previous_version_in_history"
    """An existing note became ``restricted``: its earlier, less sensitive version stays readable in git history."""


PREVIOUS_VERSION_IN_HISTORY_MESSAGE: Final = (
    "The note is now restricted, but its previous version remains in the content repository's git history and in "
    "any backups. History is never rewritten; scrubbing it is a manual owner operation."
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplyWarning:
    """A successful write the owner should still know the consequences of. Fixed text: no note content."""

    code: ApplyWarningCode
    message: str


def sensitivity_raise_warnings(previous: Sensitivity | None, new: Sensitivity) -> tuple[ApplyWarning, ...]:
    """Warn when an existing note (``previous`` is not ``None``) rises to ``restricted`` from a lower level."""
    if previous is not None and previous < Sensitivity.RESTRICTED and new is Sensitivity.RESTRICTED:
        return (
            ApplyWarning(
                code=ApplyWarningCode.PREVIOUS_VERSION_IN_HISTORY, message=PREVIOUS_VERSION_IN_HISTORY_MESSAGE
            ),
        )
    return ()


# --- write path: owner edits ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class OwnerEditNoteRequest:
    """The new version of a note, in full. ``expected_hash`` is the content hash the owner loaded (see ``NoteView``);
    ``None`` creates a note that must not exist yet. ``created`` and ``updated`` are ignored: the use case keeps the
    stored ``created`` and sets ``updated`` to today.
    """

    note: Note
    expected_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.note, Note):
            raise InvalidValueError("note", "expected a Note")
        if self.expected_hash is not None and not isinstance(self.expected_hash, str):
            raise InvalidValueError("expected_hash", "expected a string or None")


@dataclass(frozen=True, slots=True, kw_only=True)
class OwnerEditNoteResult:
    """``revision`` is ``None`` when validation found errors: nothing was written and ``validation`` says why."""

    note_id: NoteId
    validation: ValidationReport
    revision: str | None = None
    content_hash: str | None = None
    warnings: tuple[ApplyWarning, ...] = ()


# --- maintenance: indexes, health, tokens -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class RebuildIndexRequest:
    """No parameters: the whole content repository is re-read."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RebuildIndexResult:
    notes_indexed: int


@dataclass(frozen=True, slots=True, kw_only=True)
class HealthReportRequest:
    """No parameters: the report always covers every note and index."""


class HealthCode(StrEnum):
    """Checks of the health report that are not note validation rules (those use ``ViolationCode``)."""

    ORPHAN_NOTE = "orphan_note"
    REVIEW_OVERDUE = "review_overdue"
    SEARCH_INDEX_STALE = "search_index_stale"
    GRAPH_INDEX_STALE = "graph_index_stale"
    INDEX_STALE = "index_stale"


@dataclass(frozen=True, slots=True, kw_only=True)
class HealthIssue:
    """One problem. ``subject`` is a note id or the name of an index. ``message`` never contains
    note text or a secret value (secret findings keep the detector, the field and the line only).
    """

    subject: str
    code: ViolationCode | HealthCode
    severity: Severity
    field: str | None
    message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class HealthReportResult:
    issues: tuple[HealthIssue, ...]
    notes_checked: int
    search_index_checked: bool
    """False when the repository has more notes than one search page can list: only the graph index was compared."""

    @property
    def errors(self) -> tuple[HealthIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[HealthIssue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.WARNING)

    @property
    def ok(self) -> bool:
        """True when there are no errors (warnings are allowed)."""
        return not self.errors


@dataclass(frozen=True, slots=True, kw_only=True)
class IssueAgentTokenRequest:
    """``expires_at`` is optional (``None``: the token does not expire); it must be after the issue time."""

    agent: str
    ceiling: AccessCeiling
    scopes: frozenset[Scope]
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_agent_name(self.agent)
        if not isinstance(self.ceiling, AccessCeiling):
            raise InvalidValueError("ceiling", "expected an AccessCeiling")
        if (
            not isinstance(self.scopes, frozenset)
            or not self.scopes
            or not all(isinstance(s, Scope) for s in self.scopes)
        ):
            raise InvalidValueError("scopes", "expected a non-empty frozenset of Scope")
        if Scope.PROPOSE in self.scopes:
            raise InvalidValueError("scopes", "new credentials support read and write")
        expires = self.expires_at
        if expires is not None and (not isinstance(expires, datetime) or expires.tzinfo is None):
            raise InvalidValueError("expires_at", "expected a timezone-aware datetime")


@dataclass(frozen=True, slots=True, kw_only=True)
class IssuedToken:
    """The only place the plaintext secret ever appears, once. ``secret`` is excluded from ``repr``."""

    token_id: str
    agent: str
    ceiling: AccessCeiling
    scopes: frozenset[Scope]
    created_at: datetime
    expires_at: datetime | None
    secret: str = field(repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class ListAgentTokensRequest:
    """Enumerate credentials for local owner administration."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentTokenSummary:
    """Metadata returned to the owner even for revoked or expired tokens."""

    token_id: str
    agent: str
    ceiling: AccessCeiling
    scopes: frozenset[Scope]
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True, kw_only=True)
class RevokeAgentTokenRequest:
    token_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.token_id, str) or not self.token_id:
            raise InvalidValueError("token_id", "expected a non-empty string")


@dataclass(frozen=True, slots=True, kw_only=True)
class RevokedToken:
    token_id: str
    revoked_at: datetime
    already_revoked: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthenticateTokenRequest:
    """What an inbound adapter knows: the presented secret and the ceiling of the listener the socket belongs to
    (never read from request data). ``secret`` is excluded from ``repr``.
    """

    secret: str = field(repr=False)
    listener: AccessCeiling

    def __post_init__(self) -> None:
        if not isinstance(self.listener, AccessCeiling):
            raise InvalidValueError("listener", "expected an AccessCeiling")
