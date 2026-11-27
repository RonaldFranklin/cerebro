"""Authorization helpers shared by the use cases."""

from cerebro.application.errors import NoteNotFound, NoteReadError, PermissionDenied
from cerebro.application.ports.notes import NoteRepository
from cerebro.domain.access import AccessContext, Scope, is_visible
from cerebro.domain.enums import Sensitivity
from cerebro.domain.links import redact_wikilinks
from cerebro.domain.note import Note, NoteId


def require_scope(ctx: AccessContext, scope: Scope) -> None:
    """Raise ``PermissionDenied`` unless the principal holds ``scope`` (the owner holds every scope)."""
    if not ctx.can(scope):
        raise PermissionDenied(scope.value)


def stored_note(notes: NoteRepository, note_id: NoteId, ctx: AccessContext) -> Note | None:
    """``notes.get`` for a caller. A file that cannot be served now (``NoteReadError``: it differs from the last
    commit, an I/O error) may hold a note above the caller's ceiling, so below ``restricted`` it reads exactly like a
    missing note; at ``restricted`` nothing can be hidden and the error propagates. Its content is never served."""
    try:
        return notes.get(note_id)
    except NoteReadError:
        if ctx.ceiling.level is Sensitivity.RESTRICTED:
            raise
        return None


def visible_note(notes: NoteRepository, note_id: NoteId, ctx: AccessContext, *, include_history: bool) -> Note:
    """The note, or ``NoteNotFound`` whether it is missing or hidden (above the ceiling, or history)."""
    note = stored_note(notes, note_id, ctx)
    if note is None or not is_visible(note.sensitivity, note.status, ctx.ceiling, include_history=include_history):
        raise NoteNotFound(note_id)
    return note


def visible_id(
    notes: NoteRepository, note_id: NoteId | None, ctx: AccessContext, *, include_history: bool
) -> NoteId | None:
    """A reference echoed from a visible note is kept only if its target exists and is visible too."""
    if note_id is None:
        return None
    target = stored_note(notes, note_id, ctx)
    if target is None or not is_visible(
        target.sensitivity, target.status, ctx.ceiling, include_history=include_history
    ):
        return None
    return note_id


def require_owner(ctx: AccessContext, action: str) -> None:
    """Raise ``PermissionDenied`` unless the principal is the owner (review, edit and the steps that follow it)."""
    if not ctx.is_owner:
        raise PermissionDenied(action)


def require_restricted_owner(ctx: AccessContext, action: str) -> None:
    """Raise ``PermissionDenied`` unless the caller is the owner with the ceiling ``restricted``. Owner actions that
    could reveal or change what a lower ceiling hides require it here, whatever the inbound adapter already checked
    (ADR-0029)."""
    require_owner(ctx, action)
    if ctx.ceiling.level is not Sensitivity.RESTRICTED:
        raise PermissionDenied(f"{action} below the restricted ceiling")


def redacted_body(notes: NoteRepository, ctx: AccessContext, body: str, *, include_history: bool = False) -> str:
    """``body`` with every wikilink to a note the caller cannot see (or that does not exist) replaced by the marker.

    Every use case that serves a note body goes through this (defense in depth: the write path forbids such links).
    """
    cache: dict[NoteId, bool] = {}

    def may_see(target: NoteId) -> bool:
        if target not in cache:
            found = stored_note(notes, target, ctx)
            cache[target] = found is not None and is_visible(
                found.sensitivity, found.status, ctx.ceiling, include_history=include_history
            )
        return cache[target]

    return redact_wikilinks(body, may_see)
