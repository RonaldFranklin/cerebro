"""``ReadNote``: one note, or one section of it; hubs also list their members."""

from cerebro.application.access import redacted_body, require_scope, visible_note
from cerebro.application.dto import HubMember, NoteView, ReadNoteRequest
from cerebro.application.errors import SectionNotFound
from cerebro.application.ports.graph import GraphIndex
from cerebro.application.ports.notes import NoteRepository
from cerebro.domain.access import AccessContext, Scope, is_visible
from cerebro.domain.links import LinkKind
from cerebro.domain.note import Note, NoteId
from cerebro.domain.sections import extract_section


class ReadNote:
    """Reads one visible note. Wikilinks in the served body (whole or one section) whose target is hidden or missing
    for the reader become `REDACTED_LINK`; `hub`, `related` and `superseded_by` are filtered the same way."""

    def __init__(self, notes: NoteRepository, graph: GraphIndex) -> None:
        self._notes = notes
        self._graph = graph

    def execute(self, request: ReadNoteRequest, ctx: AccessContext) -> NoteView:
        require_scope(ctx, Scope.READ)
        note = visible_note(self._notes, request.note_id, ctx, include_history=request.include_history)
        body = note.body
        if request.section is not None:
            found = extract_section(note.body, request.section)
            if found is None:
                raise SectionNotFound(note.id, request.section)
            body = found
        body = redacted_body(self._notes, ctx, body, include_history=request.include_history)
        members = self._members(note, ctx, request.include_history) if note.is_hub else None
        return NoteView.from_note(
            note,
            body=body,
            section=request.section,
            members=members,
            hub=self._visible_id(note.hub, ctx, request.include_history),
            related=tuple(r for r in note.related if self._visible_id(r, ctx, request.include_history) is not None),
            superseded_by=self._visible_id(note.superseded_by, ctx, request.include_history),
        )

    def _visible_id(self, note_id: NoteId | None, ctx: AccessContext, include_history: bool) -> NoteId | None:
        """A reference echoed from a visible note is kept only if its target is visible too (and exists)."""
        if note_id is None:
            return None
        target = self._notes.get(note_id)
        if target is None or not is_visible(
            target.sensitivity, target.status, ctx.ceiling, include_history=include_history
        ):
            return None
        return note_id

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
