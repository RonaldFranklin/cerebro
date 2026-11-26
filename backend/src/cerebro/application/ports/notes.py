"""``NoteRepository``: the notes of the content repository."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from cerebro.domain.commit_message import AgentWriteCommit, OwnerCommit
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


@dataclass(frozen=True, slots=True, kw_only=True)
class CommittedCreate:
    """A committed agent create: its revision and the note exactly as that commit stored it (ADR-0024)."""

    revision: str
    note: Note


@dataclass(frozen=True, slots=True)
class FileProblem:
    """A file of the content repository that cannot be used as a note: its relative ``path`` and a fixed ``reason``
    (never note content).

    ``stable`` is true when the problem follows from the committed content alone (the file equals ``HEAD``, so the
    same revision always gives the same problem: malformed or undecryptable content, a wrong folder or file name,
    a symlink, an oversized file, the same id in several folders). An index built without such a file can be recorded
    as built from ``head()``. Anything else (a file that differs from ``HEAD``, an I/O error) is not stable."""

    path: str
    reason: str
    stable: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteScan:
    """Every usable note, ordered by id, and every file left out of ``notes`` with the reason."""

    notes: tuple[Note, ...]
    problems: tuple[FileProblem, ...] = ()

    @property
    def stable(self) -> bool:
        """``notes`` is a function of ``head()``: every problem is stable."""
        return all(problem.stable for problem in self.problems)


def check_agent_commit(changes: Sequence[NoteChange], message: AgentWriteCommit) -> None:
    """Keep the request key and authenticated attribution tied to the single change committed."""
    if len(changes) != 1 or changes[0].note.id != message.note_id:
        raise InvalidValueError("changes", "agent writes must identify exactly one changed note")
    if (changes[0].expected_hash is None) != (message.request_key is not None):
        raise InvalidValueError("request", "a request key is required only on create")


class NoteRepository(Protocol):
    """Loads and saves notes. It knows nothing about access: use cases apply the ceiling (``is_visible``) and
    raise ``NoteNotFound`` for hidden notes exactly as for missing ones.
    """

    def get(self, note_id: NoteId) -> Note | None:
        """The stored note, or ``None`` if it does not exist or its file has a stable problem (see ``scan``).
        Raises ``NoteReadError`` when the file cannot be served now (it differs from ``HEAD``, an I/O error)."""
        ...

    def list_refs(self) -> tuple[NoteRef, ...]:
        """Body-free views of every note, ordered by id (the cross-note validation snapshot)."""
        ...

    def list_notes(self) -> tuple[Note, ...]:
        """Every note, ordered by id. Strict: raises ``NoteReadError`` naming every problem of ``scan`` (a
        cross-note check over an incomplete set could pass a rule the full set fails)."""
        ...

    def scan(self) -> NoteScan:
        """Every usable note and every problem file, for the index rebuild and the health report. A file with a
        stable problem is left out and listed, and so is every copy of an id stored more than once; ``get``
        returns ``None`` for such an id. Raises ``NoteReadError`` only when the scan itself cannot run."""
        ...

    def head(self) -> str | None:
        """The current content revision: the id the latest ``apply`` returned, or ``None`` for an empty repository.

        Stable across reads; it changes only when ``apply`` commits. Indexes record it to detect staleness.
        """
        ...

    def find_create(self, request_key: str) -> CommittedCreate | None:
        """The create committed with this request key on the current history, with the note as it was then
        committed, independently of derived index state. ``None`` if no commit carries the key."""
        ...

    def apply(self, changes: Sequence[NoteChange], message: OwnerCommit | AgentWriteCommit) -> str:
        """Write all ``changes`` and record them as **one** commit with ``message``; return the revision id.

        All-or-nothing: if any change fails its check, nothing is written and nothing is committed.
        Raises ``ContentConflict`` when a stored note's hash differs from ``expected_hash`` (or a note that is
        being created already exists, or an update targets a note that does not exist), and
        ``InvalidValueError`` when ``changes`` is empty or names the same note twice.
        """
        ...
