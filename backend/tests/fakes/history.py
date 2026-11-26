from cerebro.application.history import ChangeUnavailable, HistoryVersion
from cerebro.domain.note import Note, NoteId


class FakeHistory:
    revision = "a" * 40
    parent = "b" * 40

    def __init__(self, before: Note, after: Note) -> None:
        self.before = before
        self.after = after
        self.unreadable: set[str] = set()
        """Revisions whose blobs cannot be decoded: ``note_at`` fails closed, like the git adapter."""

    def recent(self, cursor: str | None = None, *, verify_head: str | None = None) -> tuple[str, ...]:
        if verify_head is not None:
            self._require_known(verify_head)
        if cursor is not None:
            self._require_known(cursor)
        return (self.parent,) if cursor == self.parent else (self.revision, self.parent)

    def change(self, revision: str) -> HistoryVersion | None:
        self._require_known(revision)
        if revision != self.revision:
            return None
        return HistoryVersion(revision, self.parent, self.after.id, "codex", "2026-11-26T12:00:00+00:00", "update")

    def _require_known(self, revision: str) -> None:
        if revision not in (self.revision, self.parent):
            raise ChangeUnavailable()

    def note_at(self, revision: str, note_id: NoteId) -> Note | None:
        self._require_known(revision)
        if revision in self.unreadable:
            raise ChangeUnavailable()
        return (self.before if revision == self.parent else self.after) if note_id == self.after.id else None
