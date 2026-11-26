"""``OwnerEditNote``: the owner writes a note directly (still validated, still one commit)."""

from dataclasses import replace

from cerebro.application.access import require_owner, visible_note
from cerebro.application.dto import OwnerEditNoteRequest, OwnerEditNoteResult, sensitivity_raise_warnings
from cerebro.application.errors import NoteNotFound, PermissionDenied
from cerebro.application.index_state import advance_revision
from cerebro.application.note_validation import validate_candidate
from cerebro.application.ports.graph import GraphIndex
from cerebro.application.ports.notes import NoteChange, NoteRepository
from cerebro.application.ports.search import SearchIndex
from cerebro.application.ports.system import Clock, IdGenerator
from cerebro.domain.access import AccessContext, is_visible
from cerebro.domain.commit_message import CommitMessage
from cerebro.domain.content_hash import content_hash, ensure_unchanged
from cerebro.domain.errors import ContentConflict
from cerebro.domain.note import Note
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.report import OWNER_EDIT_POLICY
from cerebro.domain.vocabulary import Vocabulary

EDIT_ID_PREFIX = "owner-edit-"


class OwnerEditNote:
    """Save a validated note with owner attribution.

    * Optimistic concurrency: ``expected_hash`` must be the hash of the stored note, else
      ``ContentConflict`` and nothing is written. ``expected_hash=None`` creates a note and fails with
      ``ContentConflict`` if the id exists (``NoteNotFound`` if it exists above the owner's ceiling).
    * ``id`` is immutable (it is the key), ``created`` is kept and ``updated`` is today.
    * Validated with ``OWNER_EDIT_POLICY``: the shared note rules, with broken links as warnings, and the
      **secret scan over frontmatter and body** (errors). Errors are returned in the result and nothing is written.
    * The new sensitivity may not exceed the ceiling of the context.
    * One commit, trailers ``Proposed-by`` / ``Curated-by`` / ``Approved-by`` all ``owner`` and a generated
      ``Proposal-Id`` (``owner-edit-...``) for compatibility with earlier owner edits.
    """

    def __init__(
        self,
        notes: NoteRepository,
        search: SearchIndex,
        graph: GraphIndex,
        clock: Clock,
        ids: IdGenerator,
        vocabulary: Vocabulary,
    ) -> None:
        self._notes = notes
        self._search = search
        self._graph = graph
        self._clock = clock
        self._ids = ids
        self._vocabulary = vocabulary

    def execute(self, request: OwnerEditNoteRequest, ctx: AccessContext) -> OwnerEditNoteResult:
        require_owner(ctx, "edit notes")
        note = request.note
        today = self._clock.now().date()
        base: Note | None = None
        if request.expected_hash is None:
            existing = self._notes.get(note.id)
            if existing is not None:
                if not is_visible(existing.sensitivity, existing.status, ctx.ceiling, include_history=True):
                    raise NoteNotFound(note.id)
                raise ContentConflict("", content_hash(existing))
            candidate = replace(note, created=today, updated=today)
            verb = "Add"
        else:
            current = visible_note(self._notes, note.id, ctx, include_history=True)
            ensure_unchanged(request.expected_hash, current)
            base = current
            candidate = replace(note, created=current.created, updated=today)
            verb = "Update"
        if not ctx.ceiling.allows(candidate.sensitivity):
            raise PermissionDenied("write above the access ceiling")
        report = validate_candidate(
            candidate,
            ctx=ctx,
            notes=self._notes,
            vocabulary=self._vocabulary,
            policy=OWNER_EDIT_POLICY,
            base=base,
            forbid_lowering=False,  # the owner may declassify
        )
        if not report.ok:
            return OwnerEditNoteResult(note_id=note.id, validation=report)
        message = CommitMessage.for_owner_edit(
            subject=f"{verb} note: {note.id.value}", edit_id=f"{EDIT_ID_PREFIX}{self._ids.new_edit_id()}"
        )
        previous = self._notes.head()
        revision = self._notes.apply([NoteChange(note=candidate, expected_hash=request.expected_hash)], message)
        self._search.upsert(candidate)
        self._graph.upsert(NoteRef.from_note(candidate))
        advance_revision(self._search, self._graph, previous=previous, current=revision)
        return OwnerEditNoteResult(
            note_id=note.id,
            validation=report,
            revision=revision,
            content_hash=content_hash(candidate),
            warnings=sensitivity_raise_warnings(None if base is None else base.sensitivity, candidate.sensitivity),
        )
