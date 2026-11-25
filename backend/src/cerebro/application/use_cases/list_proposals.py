"""The local owner's Inbox and deliberate preview of a reviewable proposal."""

from cerebro.application.access import require_owner
from cerebro.application.dto import InboxItem, InboxPreview, InboxResult, ListProposalsRequest, NoteView
from cerebro.application.errors import PermissionDenied, ProposalNotApplicable, ProposalNotFound
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.proposals import ProposalStore
from cerebro.application.ports.system import Clock
from cerebro.application.proposal_support import build_candidate, provisional_note_id
from cerebro.domain.access import AccessContext
from cerebro.domain.enums import Sensitivity
from cerebro.domain.proposal import Proposal, ProposalKind, ProposalStatus


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


class ListProposals:
    def __init__(self, proposals: ProposalStore, notes: NoteRepository, clock: Clock) -> None:
        self._proposals = proposals
        self._notes = notes
        self._clock = clock

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
        base = self._notes.get(proposal.target) if proposal.target else None
        candidate, _ = build_candidate(proposal, note_id=target, base=base, today=self._clock.now().date())
        view = None
        if candidate is not None:
            view = NoteView.from_note(
                candidate,
                body=candidate.body,
                hub=candidate.hub,
                related=candidate.related,
                superseded_by=candidate.superseded_by,
            )
        return InboxResult(
            items=(_metadata(proposal),), preview=InboxPreview(decision=proposal.decision, candidate=view)
        )
