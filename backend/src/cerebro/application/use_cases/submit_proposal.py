"""``SubmitProposal``: an agent asks for a change. The only way an agent influences the content repository."""

from cerebro.application.access import require_scope, visible_note
from cerebro.application.dto import SubmitProposalRequest
from cerebro.application.errors import PermissionDenied
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.system import Clock, IdGenerator
from cerebro.application.proposal_support import scrubbed, secret_findings
from cerebro.domain.access import AccessContext, Agent, Scope
from cerebro.domain.proposal import Proposal, ProposalKind
from cerebro.domain.report import PROPOSAL_POLICY


class SubmitProposal:
    """Prepares an immutable submission for the admission pipeline.

    * Requires scope ``propose`` **and** an ``Agent`` principal: the owner edits through ``OwnerEditNote`` and may not
      submit proposals, so nobody can appear in the audit trail as an agent.
    * ``source_agent`` is the name in the access context; the request has no such field.
    * The target of ``update`` / ``link`` / ``deprecate`` must be visible to the agent (history included), else
      ``NoteNotFound``, exactly as if it did not exist. A suggested sensitivity above the agent's ceiling is refused.
    * Free text is screened for secrets before anything is stored: a hit stores a **scrubbed**, already ``rejected``
      proposal, so a secret never reaches the proposal store.
    """

    def __init__(self, notes: NoteRepository, clock: Clock, ids: IdGenerator) -> None:
        self._notes = notes
        self._clock = clock
        self._ids = ids

    def execute(self, request: SubmitProposalRequest, ctx: AccessContext) -> Proposal:
        """Construct the received or scrubbed rejection in request-local memory."""
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
            return scrubbed(proposal).reject_invalid(report, now)
        return proposal
