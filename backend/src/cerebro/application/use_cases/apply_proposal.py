"""``ApplyProposal``: writes an approved proposal to the content repository, as one commit."""

from cerebro.application.access import require_owner, visible_note
from cerebro.application.dto import ApplyOutcome, ApplyProposalResult, ProposalIdRequest
from cerebro.application.errors import NoteNotFound, PermissionDenied, ProposalNotApplicable, ProposalNotFound
from cerebro.application.ports.graph import GraphIndex
from cerebro.application.ports.notes import NoteChange, NoteRepository
from cerebro.application.ports.proposals import ProposalStore
from cerebro.application.ports.search import SearchIndex
from cerebro.application.ports.system import Clock
from cerebro.application.proposal_support import build_candidate, commit_target, is_noop, validate_candidate
from cerebro.domain.access import AccessContext, is_visible
from cerebro.domain.content_hash import ensure_unchanged
from cerebro.domain.errors import ContentConflict, InvalidTransition
from cerebro.domain.note import Note, NoteId
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.proposal import Proposal, ProposalKind, ProposalStatus
from cerebro.domain.report import PROPOSAL_POLICY, ValidationReport
from cerebro.domain.vocabulary import Vocabulary


class ApplyProposal:
    """Owner only. ``approved`` -> ``applied``, after these checks, in this order, all before anything is written:

    1. the proposal and its decision fit together (``commit_target``: a decision can never redirect a change to
       another note than ``proposal.target``);
    2. optimistic concurrency: the target still has ``base_hash`` (a ``create`` target must not exist yet). On a
       mismatch the proposal goes back to ``needs_owner`` with ``conflict`` set and **nothing is written**;
    3. the note the proposal produces is validated again with ``PROPOSAL_POLICY`` (rules may have changed since
       validation); errors send the proposal back to ``needs_owner`` as well. Unvalidated content is never written.

    Then one commit with the trailers of ``Proposal.commit_message``, the proposal is marked ``applied`` and the
    derived indexes are updated (the repository is the source of truth; ``RebuildIndex`` repairs the indexes).

    **Retries.** ``ProposalStore.save`` has no concurrency control, so a crash or a concurrent call can leave an
    ``approved`` proposal whose change is already committed. Applying again then finds the target changed, but
    ``is_noop`` shows that writing the proposal would change nothing: it is marked ``applied`` without a second
    commit (``ALREADY_APPLIED``). Two simultaneous calls are serialized by the repository's compare-and-swap, so
    at most one commit is made. An already ``applied`` proposal returns ``ALREADY_APPLIED`` and touches nothing.
    """

    def __init__(
        self,
        notes: NoteRepository,
        proposals: ProposalStore,
        search: SearchIndex,
        graph: GraphIndex,
        clock: Clock,
        vocabulary: Vocabulary,
    ) -> None:
        self._notes = notes
        self._proposals = proposals
        self._search = search
        self._graph = graph
        self._clock = clock
        self._vocabulary = vocabulary

    def execute(self, request: ProposalIdRequest, ctx: AccessContext) -> ApplyProposalResult:
        require_owner(ctx, "apply proposals")
        proposal = self._proposals.get(request.proposal_id)
        if proposal is None:
            raise ProposalNotFound(request.proposal_id)
        if proposal.status is ProposalStatus.APPLIED:
            return ApplyProposalResult(
                proposal_id=proposal.id,
                status=proposal.status,
                outcome=ApplyOutcome.ALREADY_APPLIED,
                note_id=_applied_target(proposal),
            )
        if proposal.status is not ProposalStatus.APPROVED:
            raise InvalidTransition("apply", proposal.status.value)

        note_id = commit_target(proposal)
        is_create = proposal.kind is ProposalKind.CREATE
        base = None if is_create else visible_note(self._notes, note_id, ctx, include_history=True)
        existing = self._notes.get(note_id) if is_create else None
        if existing is not None and not _visible(existing, ctx):
            raise NoteNotFound(note_id)
        today = self._clock.now().date()
        candidate, problems = build_candidate(proposal, note_id=note_id, base=base, today=today)
        if candidate is None:
            return self._send_back(proposal, ApplyOutcome.INVALID, PROPOSAL_POLICY.report(problems), note_id)
        if not ctx.ceiling.allows(candidate.sensitivity):
            raise PermissionDenied("write above the access ceiling")

        current = existing if is_create else base
        if is_create and current is not None:
            return self._conflict(proposal, candidate, current)
        if not is_create and proposal.base_hash is not None and base is not None:
            try:
                ensure_unchanged(proposal.base_hash, base)
            except ContentConflict:
                return self._conflict(proposal, candidate, base)

        report = validate_candidate(
            candidate, notes=self._notes, vocabulary=self._vocabulary, policy=PROPOSAL_POLICY, base=base
        )
        if not report.ok:
            return self._send_back(proposal, ApplyOutcome.INVALID, report, note_id)

        change = NoteChange(note=candidate, expected_hash=None if is_create else proposal.base_hash)
        try:
            revision = self._notes.apply([change], proposal.commit_message())
        except ContentConflict:
            return self._conflict(proposal, candidate, self._notes.get(note_id))
        self._proposals.save(proposal.mark_applied(self._clock.now()))
        self._reindex(candidate)
        return ApplyProposalResult(
            proposal_id=proposal.id,
            status=ProposalStatus.APPLIED,
            outcome=ApplyOutcome.APPLIED,
            note_id=note_id,
            revision=revision,
            validation=report,
        )

    def _conflict(self, proposal: Proposal, candidate: Note, current: Note | None) -> ApplyProposalResult:
        if current is not None and is_noop(candidate, current):
            self._proposals.save(proposal.mark_applied(self._clock.now()))
            self._reindex(current)
            return ApplyProposalResult(
                proposal_id=proposal.id,
                status=ProposalStatus.APPLIED,
                outcome=ApplyOutcome.ALREADY_APPLIED,
                note_id=candidate.id,
            )
        return self._send_back(proposal, ApplyOutcome.CONFLICT, None, candidate.id)

    def _send_back(
        self, proposal: Proposal, outcome: ApplyOutcome, report: ValidationReport | None, note_id: NoteId
    ) -> ApplyProposalResult:
        sent = proposal.mark_needs_owner(self._clock.now(), conflict=True)
        self._proposals.save(sent)
        return ApplyProposalResult(
            proposal_id=sent.id, status=sent.status, outcome=outcome, note_id=note_id, validation=report
        )

    def _reindex(self, note: Note) -> None:
        self._search.upsert(note)
        self._graph.upsert(NoteRef.from_note(note))


def _applied_target(proposal: Proposal) -> NoteId | None:
    try:
        return commit_target(proposal)
    except ProposalNotApplicable:  # pragma: no cover - an applied proposal always has a valid decision
        return proposal.target


def _visible(note: Note, ctx: AccessContext) -> bool:
    return is_visible(note.sensitivity, note.status, ctx.ceiling, include_history=True)
