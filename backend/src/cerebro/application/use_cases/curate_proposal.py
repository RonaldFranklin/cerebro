"""``CurateProposal``: the curator suggests a decision for a validated proposal."""

from cerebro.application.access import require_owner, visible_note
from cerebro.application.dto import ProposalIdRequest, ProposalOutcome
from cerebro.application.errors import ProposalNotFound
from cerebro.application.ports.curator import CurationRequest, Curator
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.proposals import ProposalStore
from cerebro.application.ports.search import SearchIndex
from cerebro.application.ports.system import Clock
from cerebro.application.proposal_support import provisional_note_id
from cerebro.domain.access import AccessContext
from cerebro.domain.decision import Decision, DecisionKind
from cerebro.domain.errors import InvalidTransition
from cerebro.domain.proposal import Proposal, ProposalKind, ProposalStatus

SIMILAR_LIMIT = 5


class CurateProposal:
    """``validated`` -> ``curated``, storing the curator's ``Decision`` and its name (``Curated-by``). A
    ``needs_owner`` decision goes on to the ``needs_owner`` status, the owner's queue.

    The curator only advises: it cannot write, and a decision that does not fit the proposal (it targets another
    note than the proposal does, or has the wrong shape for the kind) is replaced by ``needs_owner`` instead of
    being stored as is. Requires the owner principal (pipeline step). The note the curator sees and the similar
    notes are limited by the ceiling of the context.
    """

    def __init__(
        self, notes: NoteRepository, proposals: ProposalStore, search: SearchIndex, curator: Curator, clock: Clock
    ) -> None:
        self._notes = notes
        self._proposals = proposals
        self._search = search
        self._curator = curator
        self._clock = clock

    def execute(self, request: ProposalIdRequest, ctx: AccessContext) -> ProposalOutcome:
        require_owner(ctx, "curate proposals")
        proposal = self._proposals.get(request.proposal_id)
        if proposal is None:
            raise ProposalNotFound(request.proposal_id)
        if proposal.status is not ProposalStatus.VALIDATED:
            raise InvalidTransition("curate", proposal.status.value)
        target = None
        if proposal.kind is not ProposalKind.CREATE and proposal.target is not None:
            target = visible_note(self._notes, proposal.target, ctx, include_history=True)
        title = proposal.content.title or (target.title if target else "")
        similar = self._search.find_similar(
            f"{title} {proposal.content.summary or ''}", ceiling=ctx.ceiling, limit=SIMILAR_LIMIT
        )
        decision = self._curator.curate(CurationRequest(proposal=proposal, target=target, similar=similar))
        if not _fits(proposal, decision):
            decision = Decision.needs_owner(
                "the curator's decision does not fit the proposal: the owner decides",
                target=proposal.target or provisional_note_id(proposal.content.title),
            )
        now = self._clock.now()
        curated = proposal.mark_curated(decision, self._curator.name, now)
        if decision.kind is DecisionKind.NEEDS_OWNER:
            curated = curated.mark_needs_owner(now)  # the lifecycle only lets the owner approve from there
        self._proposals.save(curated)
        return ProposalOutcome(proposal_id=curated.id, status=curated.status, decision=decision)


def _fits(proposal: Proposal, decision: Decision) -> bool:
    if decision.kind is DecisionKind.REJECT:
        return True
    if proposal.kind is ProposalKind.CREATE:
        return decision.kind is not DecisionKind.MERGE_INTO
    return decision.kind is not DecisionKind.CREATE and decision.target in (None, proposal.target)
