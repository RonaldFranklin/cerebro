"""``SubmitProposal``: an agent asks for a change. The only way an agent influences the content repository."""

from cerebro.application.access import require_scope, visible_note
from cerebro.application.dto import ProposalOutcome, SubmitProposalRequest
from cerebro.application.errors import PermissionDenied
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.proposals import ProposalStore
from cerebro.application.ports.system import Clock, IdGenerator
from cerebro.application.proposal_support import scrubbed, secret_findings
from cerebro.domain.access import AccessContext, Agent, Scope
from cerebro.domain.proposal import Proposal, ProposalKind
from cerebro.domain.report import PROPOSAL_POLICY


class SubmitProposal:
    """Stores a ``received`` proposal.

    * Requires scope ``propose`` **and** an ``Agent`` principal: the owner edits through ``OwnerEditNote`` and may not
      submit proposals, so nobody can appear in the audit trail as an agent.
    * ``source_agent`` is the name in the access context; the request has no such field.
    * The target of ``update`` / ``link`` / ``deprecate`` must be visible to the agent (history included), else
      ``NoteNotFound``, exactly as if it did not exist. A suggested sensitivity above the agent's ceiling is refused.
    * Free text is screened for secrets before anything is stored: a hit stores a **scrubbed**, already ``rejected``
      proposal, so a secret never reaches the proposal store.
    """

    def __init__(self, notes: NoteRepository, proposals: ProposalStore, clock: Clock, ids: IdGenerator) -> None:
        self._notes = notes
        self._proposals = proposals
        self._clock = clock
        self._ids = ids

    def execute(self, request: SubmitProposalRequest, ctx: AccessContext) -> ProposalOutcome:
        require_scope(ctx, Scope.PROPOSE)
        if not isinstance(ctx.principal, Agent):
            raise PermissionDenied("propose as the owner")
        content = request.content
        if content.sensitivity is not None and not ctx.ceiling.allows(content.sensitivity):
            raise PermissionDenied("propose above the access ceiling")
        if request.kind is not ProposalKind.CREATE and request.target is not None:
            visible_note(self._notes, request.target, ctx, include_history=True)
        now = self._clock.now()
        proposal = Proposal(
            id=self._ids.new_proposal_id(),
            kind=request.kind,
            target=request.target,
            content=content,
            source_agent=ctx.principal.name,
            source_session=request.source_session,
            source_context=request.source_context,
            created_at=now,
            updated_at=now,
            base_hash=request.base_hash,
        )
        found = secret_findings(proposal)
        if found:
            report = PROPOSAL_POLICY.report(found)
            proposal = scrubbed(proposal).reject_invalid(report, now)
            self._proposals.add(proposal)
            return ProposalOutcome(proposal_id=proposal.id, status=proposal.status, validation=report)
        self._proposals.add(proposal)
        return ProposalOutcome(proposal_id=proposal.id, status=proposal.status)
