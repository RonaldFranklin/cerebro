"""Authorization helpers shared by the use cases."""

from cerebro.application.errors import NoteNotFound, PermissionDenied
from cerebro.application.ports.notes import NoteRepository
from cerebro.domain.access import AccessContext, Scope, is_visible
from cerebro.domain.links import redact_wikilinks
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


def visible_id(
    notes: NoteRepository, note_id: NoteId | None, ctx: AccessContext, *, include_history: bool
) -> NoteId | None:
    """A reference echoed from a visible note is kept only if its target exists and is visible too."""
    if note_id is None:
        return None
    target = notes.get(note_id)
    if target is None or not is_visible(
        target.sensitivity, target.status, ctx.ceiling, include_history=include_history
    ):
        return None
    return note_id


def require_owner(ctx: AccessContext, action: str) -> None:
    """Raise ``PermissionDenied`` unless the principal is the owner (review, edit and the steps that follow it)."""
    if not ctx.is_owner:
        raise PermissionDenied(action)


def redacted_body(notes: NoteRepository, ctx: AccessContext, body: str, *, include_history: bool = False) -> str:
    """``body`` with every wikilink to a note the caller cannot see (or that does not exist) replaced by the marker.

    Every use case that serves a note body goes through this (defense in depth: the write path forbids such links).
    """
    cache: dict[NoteId, bool] = {}

    def may_see(target: NoteId) -> bool:
        if target not in cache:
            found = notes.get(target)
            cache[target] = found is not None and is_visible(
                found.sensitivity, found.status, ctx.ceiling, include_history=include_history
            )
        return cache[target]

    return redact_wikilinks(body, may_see)
