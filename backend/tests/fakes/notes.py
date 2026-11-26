"""In-memory ``NoteRepository``."""

from collections.abc import Sequence

from cerebro.application.ports.notes import CommittedCreate, NoteChange, check_agent_commit
from cerebro.domain.commit_message import AgentWriteCommit, OwnerCommit
from cerebro.domain.content_hash import content_hash, is_content_hash
from cerebro.domain.errors import ContentConflict, InvalidValueError
from cerebro.domain.note import Note, NoteId
from cerebro.domain.note_ref import NoteRef


class InMemoryNoteRepository:
    def __init__(self) -> None:
        self._notes: dict[NoteId, Note] = {}
        self.commits: list[tuple[str, OwnerCommit | AgentWriteCommit, tuple[NoteId, ...]]] = []
        self._creates: dict[str, CommittedCreate] = {}

    def get(self, note_id: NoteId) -> Note | None:
        return self._notes.get(note_id)

    def list_refs(self) -> tuple[NoteRef, ...]:
        return tuple(NoteRef.from_note(n) for n in self.list_notes())

    def list_notes(self) -> tuple[Note, ...]:
        return tuple(self._notes[i] for i in sorted(self._notes))

    def head(self) -> str | None:
        return self.commits[-1][0] if self.commits else None

    def find_create(self, request_key: str) -> CommittedCreate | None:
        if not is_content_hash(request_key):
            raise InvalidValueError("request_key", "expected a SHA-256 digest")
        return self._creates.get(request_key)

    def apply(self, changes: Sequence[NoteChange], message: OwnerCommit | AgentWriteCommit) -> str:
        if isinstance(message, AgentWriteCommit):
            check_agent_commit(changes, message)
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
        if isinstance(message, AgentWriteCommit) and message.request_key is not None:
            self._creates[message.request_key] = CommittedCreate(revision=revision, note=changes[0].note)
        return revision
