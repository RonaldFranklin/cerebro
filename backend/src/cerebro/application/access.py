"""Authorization helpers shared by the use cases."""

from cerebro.application.errors import NoteNotFound, PermissionDenied
from cerebro.application.ports.notes import NoteRepository
from cerebro.domain.access import AccessContext, Scope, is_visible
from cerebro.domain.note import Note, NoteId


def require_scope(ctx: AccessContext, scope: Scope) -> None:
    """Raise ``PermissionDenied`` unless the principal holds ``scope`` (the owner holds every scope)."""
    if not ctx.can(scope):
        raise PermissionDenied(scope.value)


def visible_note(notes: NoteRepository, note_id: NoteId, ctx: AccessContext, *, include_history: bool) -> Note:
    """The note, or ``NoteNotFound`` whether it is missing or hidden (above the ceiling, or history)."""
    note = notes.get(note_id)
    if note is None or not is_visible(note.sensitivity, note.status, ctx.ceiling, include_history=include_history):
        raise NoteNotFound(note_id)
    return note
