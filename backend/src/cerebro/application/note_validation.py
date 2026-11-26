"""Shared validation of candidate notes against the content repository."""

from collections.abc import Iterator

from cerebro.application.errors import NoteReadError
from cerebro.application.ports.notes import NoteRepository, NoteScan
from cerebro.domain.access import AccessContext, is_visible
from cerebro.domain.budgets import core_budget_findings, word_count
from cerebro.domain.cross_note import cross_note_findings, inbound_link_sensitivity_findings
from cerebro.domain.enums import Priority
from cerebro.domain.links import loose_link_targets
from cerebro.domain.note import Note, NoteId
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.report import Finding, SeverityPolicy, ValidationReport, ViolationCode
from cerebro.domain.validation import validate_note
from cerebro.domain.vocabulary import Vocabulary


def references(note: Note) -> Iterator[tuple[str, str, NoteId]]:
    """``(field, position, target)`` of every reference, including each ``[[...]]`` that redaction treats as one."""
    if note.hub is not None:
        yield "hub", "The hub id", note.hub
    for number, ref in enumerate(note.related, 1):
        yield "related", f"Related entry #{number}", ref
    if note.superseded_by is not None:
        yield "superseded_by", "The superseded_by id", note.superseded_by
    for field, text in (("title", note.title), ("summary", note.summary), ("body", note.body)):
        # Split on "\n" only: a link cannot contain one, but may contain other line separators.
        for number, line in enumerate(text.split("\n"), 1):
            for ref in loose_link_targets(line):
                yield field, f"Line {number} of {field}", ref


def write_snapshot(notes: NoteRepository) -> NoteScan:
    scan = notes.scan()
    if not scan.stable:
        raise NoteReadError(problems=[(p.path, p.reason) for p in scan.problems if not p.stable])
    return scan


def validate_candidate(
    note: Note,
    *,
    ctx: AccessContext,
    scan: NoteScan,
    vocabulary: Vocabulary,
    policy: SeverityPolicy,
    base: Note | None = None,
    forbid_lowering: bool = True,
) -> ValidationReport:
    """Every rule of the write path for ``note``, all at once: per-note rules and the secret scan, cross-note
    rules over a snapshot where ``note`` replaces (or joins) the stored notes, and the combined P0 budget.

    ``base`` is the stored version of an existing note (``None`` for a new one): inbound links are checked when a note
    is new or its sensitivity rises over ``base``, and a change may not lower it unless ``forbid_lowering`` is off.
    The secret scan treats ids of notes visible to ``ctx`` (history included) as ids, so it never reveals hidden notes.
    """
    if scan.problems:
        unavailable = scan.excluded_stems
        if note.id.value in unavailable or any(ref.value in unavailable for _, _, ref in references(note)):
            raise NoteReadError("a required note file is invalid")
    snapshot = {n.id: NoteRef.from_note(n) for n in scan.notes}
    snapshot[note.id] = NoteRef.from_note(note)
    findings: list[Finding] = list(cross_note_findings(snapshot[note.id], snapshot))
    if note.priority is Priority.P0 and note.is_current:
        words = {n.id: word_count(n.body) for n in scan.notes if n.priority is Priority.P0 and n.is_current}
        words[note.id] = word_count(note.body)
        findings.extend(core_budget_findings(words))
    if base is None or note.sensitivity > base.sensitivity:
        findings.extend(inbound_link_sensitivity_findings(snapshot[note.id], snapshot))
    if forbid_lowering and base is not None and note.sensitivity < base.sensitivity:
        findings.append(
            Finding(
                code=ViolationCode.INVALID_FIELD,
                field="sensitivity",
                message="an agent cannot lower the sensitivity of an existing note; keep the current value",
            )
        )
    # Same rule as ``visible_id`` (history included), over the snapshot already read: no lookup per scanned token.
    # The candidate's own entry does not matter: the scan always exempts the note's own id.
    readable = {
        i for i, r in snapshot.items() if is_visible(r.sensitivity, r.status, ctx.ceiling, include_history=True)
    }
    report = validate_note(note, vocabulary, policy, known_id=readable.__contains__)
    return report + policy.report(findings)
