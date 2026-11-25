"""``ReviewProposal``: the owner approves or rejects a curated proposal."""

from cerebro.application.access import require_owner, visible_note
from cerebro.application.dto import ProposalOutcome, ReviewAction, ReviewProposalRequest
from cerebro.application.errors import ProposalConflict, ProposalNotFound
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.proposals import ProposalStore
from cerebro.application.ports.system import Clock
from cerebro.application.proposal_support import commit_target, scrubbed
from cerebro.domain.access import AccessContext
from cerebro.domain.commit_message import OWNER_APPROVER
from cerebro.domain.errors import InvalidTransition
from cerebro.domain.proposal import ProposalKind, ProposalStatus


class ReviewProposal:
    """Owner only. ``curated`` | ``needs_owner`` -> ``approved`` (``Approved-by: owner``) or ``rejected``.

    * An ``override`` replaces the curator's decision before approving (the owner names the note to create, or turns a
      ``needs_owner`` into ``merge_into``). The curator stays on record as ``Curated-by``.
    * Approval is refused (``ProposalNotApplicable``) when the decision could not be applied: wrong shape for the
      kind, a target other than the proposal's, a rejection. A conflicted proposal cannot be approved at all.
    * The proposal is saved once, after every check, so a refusal changes nothing.
    """

    def __init__(self, notes: NoteRepository, proposals: ProposalStore, clock: Clock) -> None:
        self._notes = notes
        self._proposals = proposals
        self._clock = clock

    def execute(self, request: ReviewProposalRequest, ctx: AccessContext) -> ProposalOutcome:
        require_owner(ctx, "review proposals")
        proposal = self._proposals.get(request.proposal_id)
        if proposal is None:
            raise ProposalNotFound(request.proposal_id)
        expected = proposal.revision
        if request.expected_revision is not None and expected != request.expected_revision:
            raise ProposalConflict(request.proposal_id)
        now = self._clock.now()
        if request.action is ReviewAction.REJECT:
            reason = request.reason or ""
            result = scrubbed(proposal.reject(reason, now), keep_reviewed_links=True)
        else:
            if proposal.status not in (ProposalStatus.CURATED, ProposalStatus.NEEDS_OWNER):
                raise InvalidTransition("approve", proposal.status.value)
            if request.override is not None:
                proposal = proposal.override_decision(request.override, now)
            commit_target(proposal)  # raises ProposalNotApplicable
            if proposal.kind is not ProposalKind.CREATE and proposal.target is not None:
                visible_note(self._notes, proposal.target, ctx, include_history=True)
            result = proposal.approve(OWNER_APPROVER, now)
        result = self._proposals.save(result, expected_revision=expected)
        return ProposalOutcome(
            proposal_id=result.id, status=result.status, decision=result.decision, conflict=result.conflict
        )
