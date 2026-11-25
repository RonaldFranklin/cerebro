"""In-memory ``NoteRepository``."""

from collections.abc import Sequence

from cerebro.application.ports.notes import NoteChange
from cerebro.domain.commit_message import CommitMessage
from cerebro.domain.content_hash import content_hash
from cerebro.domain.errors import ContentConflict, InvalidValueError
from cerebro.domain.note import Note, NoteId
from cerebro.domain.note_ref import NoteRef


class InMemoryNoteRepository:
    def __init__(self) -> None:
        self._notes: dict[NoteId, Note] = {}
        self.commits: list[tuple[str, CommitMessage, tuple[NoteId, ...]]] = []

    def get(self, note_id: NoteId) -> Note | None:
        return self._notes.get(note_id)

    def list_refs(self) -> tuple[NoteRef, ...]:
        return tuple(NoteRef.from_note(n) for n in self.list_notes())

    def list_notes(self) -> tuple[Note, ...]:
        return tuple(self._notes[i] for i in sorted(self._notes))

    def apply(self, changes: Sequence[NoteChange], message: CommitMessage) -> str:
        if not changes:
            raise InvalidValueError("changes", "at least one change is required")
        ids = [c.note.id for c in changes]
        if len(set(ids)) != len(ids):
            raise InvalidValueError("changes", "a note appears more than once")
        for change in changes:  # check everything before writing anything
            stored = self._notes.get(change.note.id)
            if change.expected_hash is None:
                if stored is not None:
                    raise ContentConflict("", content_hash(stored))
            elif stored is None:
                raise ContentConflict(change.expected_hash, "")
            elif content_hash(stored) != change.expected_hash:
                raise ContentConflict(change.expected_hash, content_hash(stored))
        for change in changes:
            self._notes[change.note.id] = change.note
        revision = f"rev-{len(self.commits) + 1:04d}"
        self.commits.append((revision, message, tuple(ids)))
        return revision
