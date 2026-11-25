"""Requests and responses of the use cases.

Requests carry domain types (built by the inbound adapter). Responses are dedicated, body-free views unless a
note is read on purpose: they are never the ``Note`` entity, so a field added to ``Note`` cannot leak by accident.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Final

from cerebro.application.ports.graph import GraphSlice
from cerebro.application.ports.search import MAX_LIMIT, SearchHit
from cerebro.domain.access import AccessCeiling, Scope, is_visible, validate_agent_name
from cerebro.domain.content_hash import content_hash
from cerebro.domain.decision import Decision
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.links import LinkKind
from cerebro.domain.note import Note, NoteId
from cerebro.domain.proposal import MAX_TEXT_LENGTH, ProposalKind, ProposalStatus, ProposedContent
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
    def from_slice(
        cls, graph: GraphSlice, *, ceiling: AccessCeiling, include_history: bool, center: NoteId | None = None
    ) -> GraphView:
        # Nodes are re-checked against the ceiling here too (defense in depth on top of the index).
        nodes = tuple(
            GraphNode(id=n.id, title=n.title, type=n.type, priority=n.priority, status=n.status)
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
            hub=hub,
            related=related,
            superseded_by=superseded_by,
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


# --- write path: proposals ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SubmitProposalRequest:
    """What an agent sends. There is deliberately **no agent / source field**: ``source.agent`` is the name of the
    token, taken from the access context, so a payload cannot claim to come from someone else.
    """

    kind: ProposalKind
    content: ProposedContent
    target: NoteId | None = None
    base_hash: str | None = None
    source_session: str | None = None
    source_context: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProposalKind):
            raise InvalidValueError("kind", "expected a ProposalKind")
        if not isinstance(self.content, ProposedContent):
            raise InvalidValueError("content", "expected ProposedContent")
        if self.target is not None and not isinstance(self.target, NoteId):
            raise InvalidValueError("target", "expected a NoteId")
        for name, value in (
            ("base_hash", self.base_hash),
            ("source_session", self.source_session),
            ("source_context", self.source_context),
        ):
            if value is not None and not isinstance(value, str):
                raise InvalidValueError(name, "expected a string")
        for name, value in (("source_session", self.source_session), ("source_context", self.source_context)):
            if value is not None and len(value) > MAX_TEXT_LENGTH:
                raise InvalidValueError(name, f"longer than {MAX_TEXT_LENGTH} characters")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposalOutcome:
    """Where a proposal stands after a step of the write path. Never carries the proposed content."""

    proposal_id: str
    status: ProposalStatus
    validation: ValidationReport | None = None
    decision: Decision | None = None
    conflict: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposalIdRequest:
    """Request of the steps that act on a stored proposal: validate, curate and apply."""

    proposal_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.proposal_id, str) or not self.proposal_id:
            raise InvalidValueError("proposal_id", "expected a non-empty string")


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewProposalRequest:
    """The owner's verdict. ``override`` replaces the curator's decision first (e.g. to name the id of a note to
    create when the curator was the ``NullCurator``). ``reason`` is required to reject.
    """

    proposal_id: str
    action: ReviewAction
    reason: str | None = None
    override: Decision | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.proposal_id, str) or not self.proposal_id:
            raise InvalidValueError("proposal_id", "expected a non-empty string")
        if not isinstance(self.action, ReviewAction):
            raise InvalidValueError("action", "expected a ReviewAction")
        if self.reason is not None and not isinstance(self.reason, str):
            raise InvalidValueError("reason", "expected a string")
        if self.override is not None and not isinstance(self.override, Decision):
            raise InvalidValueError("override", "expected a Decision")
        if self.action is ReviewAction.REJECT and (self.reason is None or not self.reason.strip()):
            raise InvalidValueError("reason", "required to reject")
        if self.action is ReviewAction.REJECT and self.override is not None:
            raise InvalidValueError("override", "not allowed when rejecting")


class ApplyOutcome(StrEnum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    """A retry found the change already in the content repository; nothing was written."""
    CONFLICT = "conflict"
    """The target changed since ``base_hash``; the proposal went back to the owner, nothing was written."""
    INVALID = "invalid"
    """Re-validation right before writing found errors; the proposal went back to the owner, nothing was written."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplyProposalResult:
    proposal_id: str
    status: ProposalStatus
    outcome: ApplyOutcome
    note_id: NoteId | None = None
    revision: str | None = None
    validation: ValidationReport | None = None


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


# --- maintenance: indexes, health, tokens -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class RebuildIndexRequest:
    """No parameters: the whole content repository is re-read."""


@dataclass(frozen=True, slots=True, kw_only=True)
class RebuildIndexResult:
    notes_indexed: int


@dataclass(frozen=True, slots=True, kw_only=True)
class HealthReportRequest:
    """No parameters: the report always covers every note, proposal and index."""


class HealthCode(StrEnum):
    """Checks of the health report that are not note validation rules (those use ``ViolationCode``)."""

    ORPHAN_NOTE = "orphan_note"
    REVIEW_OVERDUE = "review_overdue"
    STALE_PROPOSAL = "stale_proposal"
    ORPHAN_PROPOSAL = "orphan_proposal"
    SEARCH_INDEX_STALE = "search_index_stale"
    GRAPH_INDEX_STALE = "graph_index_stale"


@dataclass(frozen=True, slots=True, kw_only=True)
class HealthIssue:
    """One problem. ``subject`` is a note id, a proposal id or the name of an index. ``message`` never contains
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
    proposals_checked: int
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
