"""Rules a single note must satisfy on its own. Rules that need other notes live with links (step 3)."""

from cerebro.domain.budgets import body_budget_report
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.note import Note
from cerebro.domain.report import Severity, ValidationReport, Violation, ViolationCode
from cerebro.domain.vocabulary import CORE, HUB, Vocabulary

MAX_TITLE_LENGTH = 120
SUMMARY_MIN_LENGTH = 40
SUMMARY_MAX_LENGTH = 300


def validate_note(note: Note, vocabulary: Vocabulary) -> ValidationReport:
    """Return every violation of the per-note rules, including the note's own body budget."""
    found: list[Violation] = []

    def add(code: ViolationCode, field: str | None, message: str, severity: Severity = Severity.ERROR) -> None:
        found.append(Violation(code=code, field=field, message=message, severity=severity))

    if not note.title.strip():
        add(ViolationCode.TITLE_BLANK, "title", "title is blank")
    elif len(note.title) > MAX_TITLE_LENGTH:
        add(ViolationCode.TITLE_TOO_LONG, "title", f"longer than {MAX_TITLE_LENGTH} characters")

    if not vocabulary.has_type(note.type):
        add(ViolationCode.UNKNOWN_TYPE, "type", f"{note.type} is not in schema/types.yaml")
    if not vocabulary.has_area(note.area):
        add(ViolationCode.UNKNOWN_AREA, "area", f"{note.area} is not in schema/areas.yaml")

    summary_length = len(note.summary.strip())
    if not SUMMARY_MIN_LENGTH <= summary_length <= SUMMARY_MAX_LENGTH:
        add(
            ViolationCode.SUMMARY_LENGTH,
            "summary",
            f"{summary_length} characters; must be {SUMMARY_MIN_LENGTH}-{SUMMARY_MAX_LENGTH}",
        )

    if (note.type == CORE) != (note.priority is Priority.P0):
        add(ViolationCode.PRIORITY_TYPE_MISMATCH, "priority", "type core and priority P0 go together")
    if (note.type == HUB) != (note.priority is Priority.P1):
        add(ViolationCode.PRIORITY_TYPE_MISMATCH, "priority", "type hub and priority P1 go together")
    if note.priority is Priority.P0 and note.sensitivity is Sensitivity.RESTRICTED:
        add(ViolationCode.CORE_RESTRICTED, "sensitivity", "P0 notes cannot be restricted")

    if note.hub is None and note.priority is not Priority.P0 and not note.is_hub:
        add(ViolationCode.HUB_REQUIRED, "hub", "required for notes other than P0 and hubs")
    if note.hub == note.id:
        add(ViolationCode.SELF_REFERENCE, "hub", "a note cannot be its own hub")

    if note.status is Status.SUPERSEDED and note.superseded_by is None:
        add(ViolationCode.SUPERSEDED_BY_REQUIRED, "superseded_by", "required when status is superseded")
    if note.status is not Status.SUPERSEDED and note.superseded_by is not None:
        add(ViolationCode.SUPERSEDED_BY_UNEXPECTED, "superseded_by", "allowed only when status is superseded")
    if note.superseded_by == note.id:
        add(ViolationCode.SELF_REFERENCE, "superseded_by", "a note cannot supersede itself")

    if note.id in note.related:
        add(ViolationCode.SELF_REFERENCE, "related", "a note cannot be related to itself")
    if len(set(note.related)) != len(note.related):
        add(ViolationCode.DUPLICATE_RELATED, "related", "duplicate ids", Severity.WARNING)

    if note.updated < note.created:
        add(ViolationCode.DATES_OUT_OF_ORDER, "updated", "updated is before created")

    return ValidationReport(tuple(found)) + body_budget_report(note)
