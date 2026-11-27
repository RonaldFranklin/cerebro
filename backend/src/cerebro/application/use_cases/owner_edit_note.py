"""``OwnerEditNote``: the owner writes a note directly (still validated, still one commit)."""

from dataclasses import replace

from cerebro.application.access import require_restricted_owner, visible_note
from cerebro.application.dto import OwnerEditNoteRequest, OwnerEditNoteResult, sensitivity_raise_warnings
from cerebro.application.index_state import index_commit
from cerebro.application.note_validation import validate_candidate, write_snapshot
from cerebro.application.ports.graph import GraphIndex
from cerebro.application.ports.notes import NoteChange, NoteRepository
from cerebro.application.ports.search import SearchIndex
from cerebro.application.ports.system import Clock
from cerebro.domain.access import AccessContext
from cerebro.domain.commit_message import OwnerCommit
from cerebro.domain.content_hash import content_hash, ensure_unchanged
from cerebro.domain.report import OWNER_EDIT_POLICY
from cerebro.domain.vocabulary import Vocabulary


class OwnerEditNote:
    """Save a validated edit of an existing note with owner attribution.

    * Optimistic concurrency: ``expected_hash`` must be the hash of the stored note, else
      ``ContentConflict`` and nothing is written.
    * ``id`` is immutable (it is the key), ``created`` is kept and ``updated`` is today.
    * Validated with ``OWNER_EDIT_POLICY``: the shared note rules, with broken links as warnings, and the
      **secret scan over frontmatter and body** (errors). Errors are returned in the result and nothing is written.
    * The owner needs the ceiling ``restricted``.
    * One commit with the trailer ``Written-by: owner`` (ADR-0023).
    """

    def __init__(
        self,
        notes: NoteRepository,
        search: SearchIndex,
        graph: GraphIndex,
        clock: Clock,
        vocabulary: Vocabulary,
    ) -> None:
        self._notes = notes
        self._search = search
        self._graph = graph
        self._clock = clock
        self._vocabulary = vocabulary

    def execute(self, request: OwnerEditNoteRequest, ctx: AccessContext) -> OwnerEditNoteResult:
        require_restricted_owner(ctx, "edit notes")
        note = request.note
        today = self._clock.now().date()
        base = visible_note(self._notes, note.id, ctx, include_history=True)
        ensure_unchanged(request.expected_hash, base)
        candidate = replace(note, created=base.created, updated=today)
        report = validate_candidate(
            candidate,
            ctx=ctx,
            scan=write_snapshot(self._notes),
            vocabulary=self._vocabulary,
            policy=OWNER_EDIT_POLICY,
            base=base,
            forbid_lowering=False,  # the owner may declassify
        )
        if not report.ok:
            return OwnerEditNoteResult(note_id=note.id, validation=report)
        message = OwnerCommit.for_update(note.id)
        previous = self._notes.head()
        revision = self._notes.apply([NoteChange(note=candidate, expected_hash=request.expected_hash)], message)
        index_commit(self._search, self._graph, candidate, previous=previous, revision=revision)
        return OwnerEditNoteResult(
            note_id=note.id,
            validation=report,
            revision=revision,
            content_hash=content_hash(candidate),
            warnings=sensitivity_raise_warnings(base.sensitivity, candidate.sensitivity),
        )
