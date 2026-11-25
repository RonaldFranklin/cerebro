"""Run the agent and owner-controlled Inbox steps before the first durable write."""

from cerebro.application.dto import AdmissionResult, SubmitProposalRequest
from cerebro.application.errors import PermissionDenied
from cerebro.application.ports.proposals import ProposalStore
from cerebro.application.ports.system import Clock
from cerebro.application.proposal_support import require_admissible
from cerebro.application.use_cases.curate_proposal import CurateProposal
from cerebro.application.use_cases.submit_proposal import SubmitProposal
from cerebro.application.use_cases.validate_proposal import ValidateProposal
from cerebro.domain.access import AccessContext
from cerebro.domain.proposal import ProposalLimits, ProposalStatus


class AdmitProposal:
    def __init__(
        self,
        submit: SubmitProposal,
        validate: ValidateProposal,
        curate: CurateProposal,
        proposals: ProposalStore,
        clock: Clock,
        limits: ProposalLimits,
    ) -> None:
        self._submit = submit
        self._validate = validate
        self._curate = curate
        self._proposals = proposals
        self._clock = clock
        self._limits = limits

    def execute(self, request: SubmitProposalRequest, ctx: AccessContext) -> AdmissionResult:
        if ctx.token_id is None:
            raise PermissionDenied("submit without an authenticated token")
        proposal = self._submit.execute(request, ctx)
        owner = AccessContext.for_owner(ctx.ceiling)
        if proposal.status is ProposalStatus.RECEIVED:
            self._submit.check_references(proposal, ctx)
            proposal = self._validate.validate(proposal, owner)
        if proposal.status is ProposalStatus.VALIDATED:
            proposal = self._curate.curate(proposal, owner)
        require_admissible(proposal)
        self._proposals.admit(proposal, token_id=ctx.token_id, limits=self._limits, clock=self._clock)
        return AdmissionResult(proposal_id=proposal.id, valid=proposal.status is ProposalStatus.NEEDS_OWNER)
