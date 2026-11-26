"""``SearchIndex``: full-text search and similarity over notes, returning summaries only (never bodies)."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from cerebro.domain.access import AccessCeiling
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.note import Note, NoteId
from cerebro.domain.vocabulary import Area, NoteType

MAX_LIMIT = 100


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchQuery:
    """A filtered search. ``ceiling`` is mandatory: the index never returns a note above it.

    Empty ``text`` lists the matching notes (ordered by priority, then id). Empty filter sets mean "any".
    ``include_history`` is needed to see ``superseded`` / ``archived`` notes.
    """

    ceiling: AccessCeiling
    text: str = ""
    areas: frozenset[Area] = frozenset()
    types: frozenset[NoteType] = frozenset()
    priorities: frozenset[Priority] = frozenset()
    statuses: frozenset[Status] = frozenset()
    include_history: bool = False
    limit: int = 20

    def __post_init__(self) -> None:
        if not isinstance(self.ceiling, AccessCeiling):
            raise InvalidValueError("ceiling", "expected an AccessCeiling")
        if not isinstance(self.text, str):
            raise InvalidValueError("text", "expected a string")
        if type(self.limit) is not int or not 1 <= self.limit <= MAX_LIMIT:
            raise InvalidValueError("limit", f"expected an integer from 1 to {MAX_LIMIT}")


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchHit:
    """A note summary. ``score`` is higher for better matches (similarity hits: in the range (0, 1])."""

    id: NoteId
    title: str
    summary: str
    area: Area
    type: NoteType
    priority: Priority
    sensitivity: Sensitivity
    status: Status
    score: float


class SearchIndex(Protocol):
    """Derived, rebuildable index. Never the source of truth."""

    def upsert(self, note: Note) -> None:
        """Index (or re-index) one note."""
        ...

    def remove(self, note_id: NoteId) -> None:
        """Drop a note from the index; unknown ids are ignored."""
        ...

    def rebuild(self, notes: Iterable[Note], *, revision: str | None = None) -> None:
        """Replace the whole index with ``notes`` atomically and record ``revision`` (the content revision the
        notes were read at; ``None`` if unknown) as the one the index was built from.
        The revision is stored atomically with the rows: a reader never sees new rows with the old revision or
        the reverse."""
        ...

    def built_from(self) -> str | None:
        """The content revision recorded by the last ``rebuild`` / ``set_revision``; ``None`` if none (a new
        index). ``upsert`` and ``remove`` never change it."""
        ...

    def set_revision(self, revision: str | None) -> None:
        """Record that the index now reflects ``revision`` (used after an incremental update)."""
        ...

    def search(self, query: SearchQuery) -> tuple[SearchHit, ...]:
        """Matching visible notes, best first.

        Ordering is fixed: ``score`` descending, then ``priority`` ascending (P0 first), then ``id``. A match in
        the title outranks a match only in the body. Text matching is case-insensitive, any-term.
        """
        ...

    def find_similar(self, text: str, *, ceiling: AccessCeiling, limit: int = 5) -> tuple[SearchHit, ...]:
        """Current notes whose title and summary are similar to ``text`` (duplicate detection), most similar first.
        Bodies are not compared; the score is the fraction of shared distinct words.

        Only notes within ``ceiling`` and with a ``current`` status are considered; notes with no similarity
        are not returned.
        """
        ...
