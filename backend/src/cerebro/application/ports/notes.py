"""``NoteRepository``: the notes of the content repository."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from cerebro.domain.commit_message import CommitMessage
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.note import Note, NoteId
from cerebro.domain.note_ref import NoteRef


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteChange:
    """One note to write in an ``apply`` call.

    ``expected_hash`` is the content hash the caller based the change on (optimistic concurrency): the write is
    refused if the stored note no longer has it. ``None`` means "this note must not exist yet" (create).
    """

    note: Note
    expected_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.note, Note):
            raise InvalidValueError("note", "expected a Note")
        if self.expected_hash is not None and not isinstance(self.expected_hash, str):
            raise InvalidValueError("expected_hash", "expected a string or None")


class NoteRepository(Protocol):
    """Loads and saves notes. It knows nothing about access: use cases apply the ceiling (``is_visible``) and
    raise ``NoteNotFound`` for hidden notes exactly as for missing ones.
    """

    def get(self, note_id: NoteId) -> Note | None:
        """The stored note, or ``None`` if it does not exist."""
        ...

    def list_refs(self) -> tuple[NoteRef, ...]:
        """Body-free views of every note, ordered by id (the cross-note validation snapshot)."""
        ...

    def list_notes(self) -> tuple[Note, ...]:
        """Every note, ordered by id (used to rebuild the indexes)."""
        ...

    def apply(self, changes: Sequence[NoteChange], message: CommitMessage) -> str:
        """Write all ``changes`` and record them as **one** commit with ``message``; return the revision id.

        All-or-nothing: if any change fails its check, nothing is written and nothing is committed.
        Raises ``ContentConflict`` when a stored note's hash differs from ``expected_hash`` (or a note that is
        being created already exists, or an update targets a note that does not exist), and
        ``InvalidValueError`` when ``changes`` is empty or names the same note twice.
        """
        ...
