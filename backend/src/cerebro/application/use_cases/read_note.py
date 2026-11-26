"""``ReadNote``: one note, or one section of it; hubs also list their members."""

from cerebro.application.access import redacted_body, require_scope, visible_id, visible_note
from cerebro.application.dto import HubMember, NoteView, ReadNoteRequest
from cerebro.application.errors import SectionNotFound
from cerebro.application.ports.graph import GraphIndex
from cerebro.application.ports.notes import NoteRepository
from cerebro.domain.access import AccessContext, Scope, is_visible
from cerebro.domain.links import LinkKind
from cerebro.domain.note import Note, NoteId
from cerebro.domain.sections import extract_section


class ReadNote:
    """Reads one note by id within the ceiling, whatever its status: the view carries the status, and a note found
    through history search, a link or a write must be readable (and updatable) by id. Wikilinks in the served body
    (whole or one section) whose target is above the ceiling or missing become `REDACTED_LINK`; `hub`, `related` and
    `superseded_by` are filtered the same way. ``include_history`` only adds history members to a hub's list."""

    def __init__(self, notes: NoteRepository, graph: GraphIndex) -> None:
        self._notes = notes
        self._graph = graph

    def execute(self, request: ReadNoteRequest, ctx: AccessContext) -> NoteView:
        require_scope(ctx, Scope.READ)
        note = visible_note(self._notes, request.note_id, ctx, include_history=True)
        body = note.body
        if request.section is not None:
            found = extract_section(note.body, request.section)
            if found is None:
                raise SectionNotFound(note.id, request.section)
            body = found
        body = redacted_body(self._notes, ctx, body, include_history=True)
        members = self._members(note, ctx, request.include_history) if note.is_hub else None
        return NoteView.from_note(
            note,
            body=body,
            section=request.section,
            members=members,
            visible_title=redacted_body(self._notes, ctx, note.title, include_history=True),
            visible_summary=redacted_body(self._notes, ctx, note.summary, include_history=True),
            authenticated_writer=ctx.token_id is not None and ctx.can(Scope.WRITE),
            hub=self._visible_id(note.hub, ctx),
            related=tuple(r for r in note.related if self._visible_id(r, ctx) is not None),
            superseded_by=self._visible_id(note.superseded_by, ctx),
        )

    def _visible_id(self, note_id: NoteId | None, ctx: AccessContext) -> NoteId | None:
        return visible_id(self._notes, note_id, ctx, include_history=True)

    def _members(self, hub: Note, ctx: AccessContext, include_history: bool) -> tuple[HubMember, ...]:
        """Notes whose ``hub`` is this hub, computed now from the index and filtered by the ceiling (ADR-0015)."""
        edges = self._graph.backlinks(hub.id, ceiling=ctx.ceiling, include_history=include_history)
        members: list[Note] = []
        for source in sorted({e.source for e in edges if e.kind is LinkKind.HUB}):
            note = self._notes.get(source)
            if (
                note is not None
                and note.hub == hub.id
                and is_visible(note.sensitivity, note.status, ctx.ceiling, include_history=include_history)
            ):
                members.append(note)
        members.sort(key=lambda n: (n.priority, n.title, n.id))
        return tuple(HubMember.from_note(n) for n in members)
