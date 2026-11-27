"""``CheckOwnerEdit``: what would happen to an owner edit, without writing it."""

from dataclasses import replace

from cerebro.application.access import require_owner, visible_note
from cerebro.application.dto import CheckOwnerEditResult, OwnerEditNoteRequest
from cerebro.application.errors import PermissionDenied
from cerebro.application.note_validation import validate_candidate, write_snapshot
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.system import Clock
from cerebro.domain.access import AccessContext
from cerebro.domain.budgets import body_limits, word_count
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.links import loose_link_targets
from cerebro.domain.note import Note, NoteId
from cerebro.domain.report import OWNER_EDIT_POLICY
from cerebro.domain.vocabulary import Vocabulary


class CheckOwnerEdit:
    """The dry run of ``OwnerEditNote``: the same rules and severities over the same snapshot, plus what the editor
    shows beside them (the budget of the note's type, the body lines holding a bad link, whether the base is still
    current). Nothing is written.

    Owner only, with the ceiling ``restricted``: a check tells missing link targets from more sensitive ones, so on a
    lower ceiling it would reveal what the ceiling hides.
    """

    def __init__(self, notes: NoteRepository, clock: Clock, vocabulary: Vocabulary) -> None:
        self._notes = notes
        self._clock = clock
        self._vocabulary = vocabulary

    def execute(self, request: OwnerEditNoteRequest, ctx: AccessContext) -> CheckOwnerEditResult:
        require_owner(ctx, "check an edit")
        if ctx.ceiling.level is not Sensitivity.RESTRICTED:
            raise PermissionDenied("check an edit below the restricted ceiling")
        base = visible_note(self._notes, request.note.id, ctx, include_history=True)
        candidate = replace(request.note, created=base.created, updated=self._clock.now().date())
        scan = write_snapshot(self._notes)
        report = validate_candidate(
            candidate,
            ctx=ctx,
            scan=scan,
            vocabulary=self._vocabulary,
            policy=OWNER_EDIT_POLICY,
            base=base,
            forbid_lowering=False,
        )
        soft, hard = body_limits(candidate)
        words = word_count(candidate.body)
        if candidate.priority is Priority.P0 and candidate.is_current:
            # Core notes share one budget.
            words += sum(
                word_count(n.body)
                for n in scan.notes
                if n.priority is Priority.P0 and n.is_current and n.id != candidate.id
            )
        return CheckOwnerEditResult(
            validation=report,
            words=words,
            soft_limit=soft,
            hard_limit=hard,
            link_lines=_bad_link_lines(candidate, {n.id: n for n in scan.notes} | {candidate.id: candidate}),
            base_current=content_hash(base) == request.expected_hash,
        )


def _bad_link_lines(note: Note, notes: dict[NoteId, Note]) -> tuple[int, ...]:
    """1-based body lines with a link to a note that is missing or more sensitive than ``note``."""
    return tuple(
        number
        for number, line in enumerate(note.body.split("\n"), 1)
        if any(ref not in notes or notes[ref].sensitivity > note.sensitivity for ref in loose_link_targets(line))
    )
