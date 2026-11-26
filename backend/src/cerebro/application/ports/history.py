"""Bounded, read-only access to attributable changes in the content history."""

from typing import Protocol

from cerebro.application.history import HistoryVersion
from cerebro.domain.note import Note, NoteId


class ChangeHistory(Protocol):
    def recent(self, cursor: str | None = None, *, verify_head: str | None = None) -> tuple[str, ...]:
        """Start at cursor (or HEAD), returning at most RECENT_WINDOW revisions plus one continuation id.

        The caller supplies the start revision from an authenticated first-parent continuation."""
        ...

    def change(self, revision: str) -> HistoryVersion | None:
        """An attributable single-note create/update, or None for an unrelated change."""
        ...

    def note_at(self, revision: str, note_id: NoteId) -> Note | None:
        """Decode the exact historical blob. Return None for absent notes and fail closed on unreadable ones."""
        ...
