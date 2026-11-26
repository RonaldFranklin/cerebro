"""Revise a proposal body while it is awaiting owner approval."""

from dataclasses import replace

from cerebro.application.access import require_owner
from cerebro.application.dto import AmendmentResult, AmendProposalRequest
from cerebro.application.errors import (
    AmendmentValidationError,
    PermissionDenied,
    ProposalConflict,
    ProposalNotApplicable,
    ProposalNotFound,
)
from cerebro.application.ports.proposals import ProposalStore
from cerebro.application.ports.system import Clock
from cerebro.application.proposal_support import commit_target
from cerebro.application.use_cases.validate_proposal import ValidateProposal
from cerebro.domain.access import AccessContext
from cerebro.domain.enums import Sensitivity
from cerebro.domain.proposal import ProposalKind, ProposalStatus


class AmendProposal:
    def __init__(self, proposals: ProposalStore, validator: ValidateProposal, clock: Clock) -> None:
        self._proposals = proposals
        self._validator = validator
        self._clock = clock

    def execute(self, request: AmendProposalRequest, ctx: AccessContext) -> AmendmentResult:
        require_owner(ctx, "amend proposals")
        if ctx.ceiling.level is not Sensitivity.RESTRICTED:
            raise PermissionDenied("amend proposals")
        proposal = self._proposals.get(request.proposal_id)
        if proposal is None:
            raise ProposalNotFound(request.proposal_id)
        if proposal.revision != request.expected_revision:
            raise ProposalConflict(request.proposal_id)
        if (
            proposal.status not in (ProposalStatus.CURATED, ProposalStatus.NEEDS_OWNER)
            or proposal.kind not in (ProposalKind.CREATE, ProposalKind.UPDATE)
            or proposal.conflict
        ):
            raise ProposalNotApplicable("only reviewable creates and updates may be amended")
        now = self._clock.now()
        candidate = replace(proposal, content=replace(proposal.content, body=request.body), updated_at=now)
        report = self._validator.assess(candidate, ctx, now.date(), candidate_id=commit_target(candidate))
        if not report.ok:
            raise AmendmentValidationError(report)
        saved = self._proposals.save(replace(candidate, validation=report), expected_revision=request.expected_revision)
        return AmendmentResult(proposal_id=saved.id, revision=saved.revision)
