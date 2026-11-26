"""The local owner's Inbox and deliberate preview of a reviewable proposal."""

from cerebro.application.access import require_owner
from cerebro.application.dto import InboxItem, InboxPreview, InboxResult, ListProposalsRequest, NoteView
from cerebro.application.errors import PermissionDenied, ProposalNotApplicable, ProposalNotFound
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.proposals import ProposalStore
from cerebro.application.proposal_support import (
    build_candidate,
    provisional_note_id,
    stale_proposal_base,
    validate_candidate,
)
from cerebro.domain.access import AccessContext
from cerebro.domain.enums import Sensitivity
from cerebro.domain.note import Note
from cerebro.domain.proposal import Proposal, ProposalKind, ProposalStatus
from cerebro.domain.report import PROPOSAL_POLICY
from cerebro.domain.vocabulary import Vocabulary


def _metadata(proposal: Proposal) -> InboxItem:
    return InboxItem(
        id=proposal.id,
        kind=proposal.kind,
        status=proposal.status,
        revision=proposal.revision,
        source_agent=proposal.source_agent,
        target=proposal.target,
        conflict=proposal.conflict,
    )


def _preview_note(note: Note | None) -> NoteView | None:
    if note is None:
        return None
    return NoteView.from_note(
        note, body=note.body, hub=note.hub, related=note.related, superseded_by=note.superseded_by
    )


class ListProposals:
    def __init__(self, proposals: ProposalStore, notes: NoteRepository, vocabulary: Vocabulary) -> None:
        self._proposals = proposals
        self._notes = notes
        self._vocabulary = vocabulary

    def execute(self, request: ListProposalsRequest, ctx: AccessContext) -> InboxResult:
        require_owner(ctx, "inspect the inbox")
        if ctx.ceiling.level is not Sensitivity.RESTRICTED:
            raise PermissionDenied("inspect the inbox")
        if request.proposal_id is None:
            return InboxResult(items=tuple(_metadata(proposal) for proposal in self._proposals.find()))
        proposal = self._proposals.get(request.proposal_id)
        if proposal is None:
            raise ProposalNotFound(request.proposal_id)
        if proposal.status not in (ProposalStatus.CURATED, ProposalStatus.NEEDS_OWNER):
            raise ProposalNotApplicable("only reviewable proposals have a preview")
        target = proposal.target
        if proposal.kind is ProposalKind.CREATE:
            target = (proposal.decision.target if proposal.decision else None) or provisional_note_id(
                proposal.content.title
            )
        current = self._notes.get(target) if target is not None else None
        create = proposal.kind is ProposalKind.CREATE
        base = None if create else current
        stale = stale_proposal_base(proposal, current)
        candidate, findings = build_candidate(proposal, note_id=target, base=base)
        validation = None
        if candidate is None:
            validation = PROPOSAL_POLICY.report(findings)
        elif not stale:
            validation = validate_candidate(
                candidate, notes=self._notes, vocabulary=self._vocabulary, policy=PROPOSAL_POLICY, base=base
            )
        preview = InboxPreview(
            decision=proposal.decision,
            candidate=_preview_note(candidate),
            base=_preview_note(base),
            base_label="empty" if create else ("current" if stale else "base"),
            stale_base=stale,
            validation=validation,
            historical_validation=proposal.validation,
        )
        return InboxResult(items=(_metadata(proposal),), preview=preview)
