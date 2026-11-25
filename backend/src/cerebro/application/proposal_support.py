"""Shared building blocks of the write path: from a proposal to the note it would produce, and its validation.

Everything here is a pure function of its arguments (ports are passed in), so ``ValidateProposal`` and
``ApplyProposal`` build and check **exactly the same** candidate note.
"""

import re
import unicodedata
from dataclasses import replace
from datetime import date

from cerebro.application.errors import ProposalNotApplicable
from cerebro.application.ports.notes import NoteRepository
from cerebro.domain.budgets import core_budget_findings, word_count
from cerebro.domain.cross_note import cross_note_findings
from cerebro.domain.decision import DecisionKind
from cerebro.domain.enums import Priority, Status
from cerebro.domain.note import MAX_NOTE_ID_LENGTH, Note, NoteId
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.proposal import Proposal, ProposalKind, ProposedContent
from cerebro.domain.report import Finding, SeverityPolicy, ValidationReport, ViolationCode
from cerebro.domain.secrets import scan_text
from cerebro.domain.validation import validate_note
from cerebro.domain.vocabulary import Vocabulary

REDACTED_TITLE = "[redacted]"
DEFAULT_LANG = "en"


def provisional_note_id(title: str | None) -> NoteId | None:
    """A kebab-case id derived from a title (ASCII letters and digits), or ``None`` if nothing usable is left.

    Used to validate a ``create`` proposal before the curator has named the note, and as the curator's suggestion.
    """
    if not title:
        return None
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:MAX_NOTE_ID_LENGTH].strip("-")
    return NoteId(slug) if slug else None


def commit_target(proposal: Proposal) -> NoteId:
    """The id of the note a proposal's decision lets it write, or ``ProposalNotApplicable``.

    * ``create`` needs a ``create`` decision (or an owner-approved ``needs_owner`` one) that names the new id.
    * ``update`` / ``link`` / ``deprecate`` need ``merge_into`` (or owner-approved ``needs_owner``) and the
      decision's target, if it names one, must be **the proposal's own target**: a decision can never redirect
      a change to another note.
    * ``reject`` never applies, and neither does a decision of the wrong shape for the proposal kind.
    """
    decision = proposal.decision
    if decision is None:
        raise ProposalNotApplicable("no decision")
    if decision.kind is DecisionKind.REJECT:
        raise ProposalNotApplicable("the decision is a rejection")
    if proposal.kind is ProposalKind.CREATE:
        if decision.kind not in (DecisionKind.CREATE, DecisionKind.NEEDS_OWNER):
            raise ProposalNotApplicable("a create proposal needs a create decision")
        if decision.target is None:
            raise ProposalNotApplicable("the decision names no note to create")
        return decision.target
    if decision.kind not in (DecisionKind.MERGE_INTO, DecisionKind.NEEDS_OWNER):
        raise ProposalNotApplicable(f"a {proposal.kind.value} proposal needs a merge_into decision")
    if proposal.target is None or (decision.target is not None and decision.target != proposal.target):
        raise ProposalNotApplicable("the decision targets a different note than the proposal")
    return proposal.target


def _missing(field: str) -> Finding:
    return Finding(code=ViolationCode.MISSING_FIELD, field=field, message="required to create a note")


def build_candidate(
    proposal: Proposal, *, note_id: NoteId | None, base: Note | None, today: date
) -> tuple[Note | None, tuple[Finding, ...]]:
    """The note the proposal would write, or the findings that stop it from being built.

    Findings never quote proposal content. ``create`` needs title, body, type, area, priority, sensitivity and
    summary from the agent (``hub`` where the type requires one); the note starts ``current`` and English.
    The other kinds start from ``base``, the stored note, and change only what they carry: ``update`` replaces
    the body and any metadata given and adds ``related``; ``link`` only adds ``related``; ``deprecate`` makes the
    note ``superseded`` (if ``superseded_by`` is given) or ``archived``. ``related`` ids are only ever added.
    """
    c = proposal.content
    if proposal.kind is ProposalKind.CREATE:
        return _create(c, note_id, today)
    if base is None:  # callers load the target first; keep the function total
        return None, (Finding(code=ViolationCode.INVALID_FIELD, field="target", message="the target is missing"),)
    if c.superseded_by is not None and proposal.kind is not ProposalKind.DEPRECATE:
        return None, (
            Finding(code=ViolationCode.INVALID_FIELD, field="superseded_by", message="only a deprecation may set it"),
        )
    related = tuple(dict.fromkeys((*base.related, *c.related)))
    match proposal.kind:
        case ProposalKind.LINK:
            return replace(base, related=related, updated=today), ()
        case ProposalKind.DEPRECATE:
            status = Status.SUPERSEDED if c.superseded_by is not None else Status.ARCHIVED
            return replace(base, status=status, superseded_by=c.superseded_by, updated=today), ()
        case _:
            return (
                replace(
                    base,
                    title=c.title if c.title is not None else base.title,
                    type=c.type if c.type is not None else base.type,
                    area=c.area if c.area is not None else base.area,
                    priority=c.priority if c.priority is not None else base.priority,
                    sensitivity=c.sensitivity if c.sensitivity is not None else base.sensitivity,
                    summary=c.summary if c.summary is not None else base.summary,
                    hub=c.hub if c.hub is not None else base.hub,
                    body=c.body if c.body is not None else base.body,
                    related=related,
                    updated=today,
                ),
                (),
            )


def _create(c: ProposedContent, note_id: NoteId | None, today: date) -> tuple[Note | None, tuple[Finding, ...]]:
    found: list[Finding] = []
    if note_id is None:
        found.append(
            Finding(code=ViolationCode.INVALID_FIELD, field="title", message="no note id can be derived from it")
        )
    if c.superseded_by is not None:
        found.append(
            Finding(code=ViolationCode.INVALID_FIELD, field="superseded_by", message="only a deprecation may set it")
        )
    for name in ("type", "area", "priority", "sensitivity", "summary"):
        if getattr(c, name) is None:
            found.append(_missing(name))
    if (
        found
        or note_id is None
        or c.type is None
        or c.area is None
        or c.priority is None
        or c.sensitivity is None
        or c.summary is None
        or c.title is None
        or c.body is None
    ):
        return None, tuple(found)
    return (
        Note(
            id=note_id,
            title=c.title,
            type=c.type,
            area=c.area,
            priority=c.priority,
            sensitivity=c.sensitivity,
            status=Status.CURRENT,
            summary=c.summary,
            hub=c.hub,
            related=c.related,
            superseded_by=None,
            lang=DEFAULT_LANG,
            created=today,
            updated=today,
            review_by=None,
            body=c.body,
        ),
        (),
    )


def validate_candidate(
    note: Note,
    *,
    notes: NoteRepository,
    vocabulary: Vocabulary,
    policy: SeverityPolicy,
    base: Note | None = None,
) -> ValidationReport:
    """Every rule of the write path for ``note``, all at once: per-note rules and the secret scan, cross-note
    rules over a snapshot where ``note`` replaces (or joins) the stored notes, and the combined P0 budget.

    ``base`` is the stored version of an existing note: a change may not lower its sensitivity.
    """
    snapshot = {ref.id: ref for ref in notes.list_refs()}
    snapshot[note.id] = NoteRef.from_note(note)
    findings: list[Finding] = list(cross_note_findings(snapshot[note.id], snapshot))
    if note.priority is Priority.P0 and note.is_current:
        words = {n.id: word_count(n.body) for n in notes.list_notes() if n.priority is Priority.P0 and n.is_current}
        words[note.id] = word_count(note.body)
        findings.extend(core_budget_findings(words))
    if base is not None and note.sensitivity < base.sensitivity:
        findings.append(
            Finding(
                code=ViolationCode.INVALID_FIELD,
                field="sensitivity",
                message="a proposal cannot lower the sensitivity of an existing note",
            )
        )
    return validate_note(note, vocabulary, policy) + policy.report(findings)


def is_noop(candidate: Note, current: Note) -> bool:
    """Whether writing ``candidate`` over ``current`` would change nothing but the ``updated`` date.

    This is how a retry recognizes that a proposal was already applied (the commit succeeded, recording it failed).
    """
    return replace(candidate, updated=current.updated, created=current.created) == current


def secret_findings(proposal: Proposal) -> tuple[Finding, ...]:
    """Secret-scanner findings over every free-text field of a proposal (never contain the secret)."""
    c = proposal.content
    texts = (
        ("title", c.title),
        ("body", c.body),
        ("summary", c.summary),
        ("rationale", c.rationale),
        ("source_session", proposal.source_session),
        ("source_context", proposal.source_context),
    )
    return tuple(f for field, text in texts if text for f in scan_text(text, field=field))


def scrubbed(proposal: Proposal) -> Proposal:
    """The proposal without any free text, so that a rejected payload that held a secret is never stored.

    Required fields keep a harmless placeholder; ids, kind, target, base hash and agent are kept for the audit trail.
    """
    c = proposal.content
    content = ProposedContent(
        title=REDACTED_TITLE if c.title is not None else None,
        body="" if c.body is not None else None,
        area=c.area,
        type=c.type,
        priority=c.priority,
        sensitivity=c.sensitivity,
        rationale="",
        related=c.related,
        superseded_by=c.superseded_by,
        summary=None,
        hub=c.hub,
    )
    return replace(proposal, content=content, source_session=None, source_context=None)
