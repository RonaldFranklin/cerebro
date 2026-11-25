"""``CurateProposal``: the curator suggests a decision for a validated proposal."""

from dataclasses import replace

from cerebro.application.access import redacted_body, require_owner, visible_id, visible_note
from cerebro.application.dto import ProposalIdRequest, ProposalOutcome
from cerebro.application.errors import ProposalNotFound
from cerebro.application.ports.curator import CurationRequest, Curator
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.proposals import ProposalStore
from cerebro.application.ports.search import SearchIndex
from cerebro.application.ports.system import Clock
from cerebro.application.proposal_support import provisional_note_id
from cerebro.domain.access import AccessCeiling, AccessContext
from cerebro.domain.decision import Decision, DecisionKind
from cerebro.domain.enums import Sensitivity
from cerebro.domain.errors import InvalidTransition
from cerebro.domain.links import loose_link_targets
from cerebro.domain.note import Note, NoteId
from cerebro.domain.proposal import Proposal, ProposalKind, ProposalStatus

SIMILAR_LIMIT = 5
CURATOR_CEILING = AccessCeiling(Sensitivity.INTERNAL)
"""The highest sensitivity the curator may ever see (ADR-0016): it is an external service."""
POLICY_CURATOR_NAME = "curator/policy"
"""``Curated-by`` of a proposal that skips the curator because it touches restricted content."""
RESTRICTED_REASON = "touches restricted content: the owner decides"


class CurateProposal:
    """``validated`` -> ``curated``, storing the curator's ``Decision`` and its name (``Curated-by``). A
    ``needs_owner`` decision goes on to the ``needs_owner`` status, the owner's queue.

    The curator only advises: it cannot write, and a decision that does not fit the proposal (it targets another
    note than the proposal does, or has the wrong shape for the kind) is replaced by ``needs_owner`` instead of
    being stored as is. Requires the owner principal (pipeline step). The note the curator sees and the similar
    notes are limited by ``min(ctx.ceiling, CURATOR_CEILING)``, and a proposal that creates, changes or links a
    restricted note never reaches the curator at all: it goes straight to ``needs_owner`` (ADR-0016).
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
        target_id = proposal.target or provisional_note_id(proposal.content.title)
        if self._touches_restricted(proposal):
            decision = Decision.needs_owner(RESTRICTED_REASON, target=target_id)
            curator_name = POLICY_CURATOR_NAME
        else:
            decision = self._ask_curator(proposal, ctx)
            curator_name = self._curator.name
        now = self._clock.now()
        curated = proposal.mark_curated(decision, curator_name, now)
        if decision.kind is DecisionKind.NEEDS_OWNER:
            curated = curated.mark_needs_owner(now)  # the lifecycle only lets the owner approve from there
        curated = self._proposals.save(curated, expected_revision=proposal.revision)
        return ProposalOutcome(proposal_id=curated.id, status=curated.status, decision=decision)

    def _ask_curator(self, proposal: Proposal, ctx: AccessContext) -> Decision:
        capped = AccessContext(
            principal=ctx.principal, ceiling=min(ctx.ceiling, CURATOR_CEILING, key=_level), scopes=ctx.scopes
        )
        target = None
        if proposal.kind is not ProposalKind.CREATE and proposal.target is not None:
            target = visible_note(self._notes, proposal.target, capped, include_history=True)
            target = self._capped_target(target, capped)
        title = proposal.content.title or (target.title if target else "")
        similar = self._search.find_similar(
            f"{title} {proposal.content.summary or ''}", ceiling=capped.ceiling, limit=SIMILAR_LIMIT
        )
        decision = self._curator.curate(CurationRequest(proposal=proposal, target=target, similar=similar))
        if not _fits(proposal, decision):
            return Decision.needs_owner(
                "the curator's decision does not fit the proposal: the owner decides",
                target=proposal.target or provisional_note_id(proposal.content.title),
            )
        return decision

    def _capped_target(self, target: Note, capped: AccessContext) -> Note:
        """The target as the curator may see it: body links and references to hidden notes removed."""

        def keep(note_id: NoteId | None) -> NoteId | None:
            return visible_id(self._notes, note_id, capped, include_history=True)

        return replace(
            target,
            body=redacted_body(self._notes, capped, target.body, include_history=True),
            hub=keep(target.hub),
            related=tuple(r for r in target.related if keep(r) is not None),
            superseded_by=keep(target.superseded_by),
        )

    def _touches_restricted(self, proposal: Proposal) -> bool:
        """Whether the proposal creates, changes or links a restricted note. Reads the repository directly (not
        through the context's ceiling): missing notes are not restricted."""
        content = proposal.content
        if content.sensitivity is Sensitivity.RESTRICTED:
            return True
        ids: list[NoteId | None] = [proposal.target, content.hub, content.superseded_by, *content.related]
        for text in (content.body, content.title, content.summary, content.rationale):
            if text:
                ids.extend(loose_link_targets(text))
        for note_id in dict.fromkeys(i for i in ids if i is not None):
            note = self._notes.get(note_id)
            if note is not None and note.sensitivity is Sensitivity.RESTRICTED:
                return True
        return False


def _level(ceiling: AccessCeiling) -> Sensitivity:
    return ceiling.level


def _fits(proposal: Proposal, decision: Decision) -> bool:
    if decision.kind is DecisionKind.REJECT:
        return True
    if proposal.kind is ProposalKind.CREATE:
        return decision.kind is not DecisionKind.MERGE_INTO
    return decision.kind is not DecisionKind.CREATE and decision.target in (None, proposal.target)
