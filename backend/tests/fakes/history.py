from cerebro.application.history import HistoryVersion
from cerebro.domain.note import Note, NoteId


class FakeHistory:
    revision = "a" * 40
    parent = "b" * 40

    def __init__(self, before: Note, after: Note) -> None:
        self.before = before
        self.after = after

    def recent(self) -> tuple[str, ...]:
        return (self.revision,)

    def change(self, revision: str) -> HistoryVersion | None:
        if revision != self.revision:
            return None
        return HistoryVersion(revision, self.parent, self.after.id, "codex", "2026-11-26T12:00:00+00:00", "update")

    def note_at(self, revision: str, note_id: NoteId) -> Note | None:
        return (self.before if revision == self.parent else self.after) if note_id == self.after.id else None
