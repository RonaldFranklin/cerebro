"""``ValidateProposal``: deterministic validation of a ``received`` proposal."""

from datetime import date

from cerebro.application.access import require_owner, visible_note
from cerebro.application.dto import ProposalIdRequest, ProposalOutcome
from cerebro.application.errors import ProposalNotFound
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.proposals import ProposalStore
from cerebro.application.ports.search import SearchIndex
from cerebro.application.ports.system import Clock
from cerebro.application.proposal_support import (
    build_candidate,
    provisional_note_id,
    scrubbed,
    secret_findings,
    validate_candidate,
)
from cerebro.domain.access import AccessContext
from cerebro.domain.errors import InvalidTransition
from cerebro.domain.note import Note
from cerebro.domain.proposal import Proposal, ProposalKind, ProposalStatus
from cerebro.domain.report import PROPOSAL_POLICY, Finding, ValidationReport, ViolationCode
from cerebro.domain.vocabulary import Vocabulary

DUPLICATE_MIN_SCORE = 0.5
"""Similarity (0-1) from which an existing note is reported as a possible duplicate of a new one."""
DUPLICATE_LIMIT = 3


class ValidateProposal:
    """``received`` -> ``validated`` (no errors; warnings kept) or ``rejected`` (with the report).

    Runs the proposal as the note it would produce (``build_candidate``) through the per-note rules, the secret
    scan over the whole note, the cross-note rules (snapshot built from ``NoteRepository.list_refs``), the combined
    P0 budget, and, for ``create``, duplicate detection through the ``SearchIndex`` (a warning, never an error).

    Requires the owner principal: validation is a step of the owner-controlled pipeline (the composition root runs
    it right after a submission), never something a token can trigger. Reports are for the owner and the curator;
    they never quote proposed content, and a rejected proposal that held a secret is stored scrubbed.
    """

    def __init__(
        self,
        notes: NoteRepository,
        proposals: ProposalStore,
        search: SearchIndex,
        clock: Clock,
        vocabulary: Vocabulary,
    ) -> None:
        self._notes = notes
        self._proposals = proposals
        self._search = search
        self._clock = clock
        self._vocabulary = vocabulary

    def execute(self, request: ProposalIdRequest, ctx: AccessContext) -> ProposalOutcome:
        require_owner(ctx, "validate proposals")
        proposal = self._proposals.get(request.proposal_id)
        if proposal is None:
            raise ProposalNotFound(request.proposal_id)
        if proposal.status is not ProposalStatus.RECEIVED:
            raise InvalidTransition("validate", proposal.status.value)
        now = self._clock.now()
        base = None
        if proposal.kind is not ProposalKind.CREATE and proposal.target is not None:
            base = visible_note(self._notes, proposal.target, ctx, include_history=True)
        report = self._report(proposal, base, now.date(), ctx)
        if report.ok:
            result = proposal.mark_validated(report, now)
        else:
            rejected = proposal.reject_invalid(report, now)
            has_secret = any(v.code is ViolationCode.SECRET_DETECTED for v in report.violations)
            result = scrubbed(rejected) if has_secret else rejected
        result = self._proposals.save(result, expected_revision=proposal.revision)
        return ProposalOutcome(proposal_id=result.id, status=result.status, validation=report)

    def _report(self, proposal: Proposal, base: Note | None, today: date, ctx: AccessContext) -> ValidationReport:
        candidate_id = proposal.target or provisional_note_id(proposal.content.title)
        candidate, problems = build_candidate(proposal, note_id=candidate_id, base=base, today=today)
        # Free text that is not part of the note (rationale, session, context) is scanned too.
        report = PROPOSAL_POLICY.report((*problems, *secret_findings(proposal)))
        if candidate is None:
            return report
        report = report + validate_candidate(
            candidate, notes=self._notes, vocabulary=self._vocabulary, policy=PROPOSAL_POLICY, base=base
        )
        if proposal.kind is ProposalKind.CREATE:
            report = report + PROPOSAL_POLICY.report(self._duplicates(candidate, ctx))
        return _dedupe(report)

    def _duplicates(self, candidate: Note, ctx: AccessContext) -> tuple[Finding, ...]:
        # The owner's ceiling: duplicates are reported to the owner and the curator, never to the submitting agent.
        hits = self._search.find_similar(
            f"{candidate.title} {candidate.summary}", ceiling=ctx.ceiling, limit=DUPLICATE_LIMIT
        )
        return tuple(
            Finding(
                code=ViolationCode.POSSIBLE_DUPLICATE,
                field="title",
                message=f"similar to {hit.id} ({hit.score:.0%})",
            )
            for hit in hits
            if hit.score >= DUPLICATE_MIN_SCORE and hit.id != candidate.id
        )


def _dedupe(report: ValidationReport) -> ValidationReport:
    """The scanner runs over the note and over the free text of the proposal: report each hit once."""
    seen: set[tuple[ViolationCode, str | None, str]] = set()
    unique = []
    for violation in report.violations:
        key = (violation.code, violation.field, violation.message)
        if key not in seen:
            seen.add(key)
            unique.append(violation)
    return ValidationReport(tuple(unique))
