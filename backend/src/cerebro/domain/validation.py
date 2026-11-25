"""Rules a single note must satisfy on its own. Rules that need other notes live in ``cross_note``."""

from cerebro.domain.budgets import body_budget_findings
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.note import Note
from cerebro.domain.report import Finding, SeverityPolicy, ValidationReport, ViolationCode
from cerebro.domain.secrets import scan_note
from cerebro.domain.vocabulary import CORE, HUB, NoteType, Vocabulary

MAX_TITLE_LENGTH = 120
SUMMARY_MIN_LENGTH = 40
SUMMARY_MAX_LENGTH = 300

_PRIORITIES_BY_TYPE: dict[NoteType, frozenset[Priority]] = {
    CORE: frozenset({Priority.P0}),
    HUB: frozenset({Priority.P1}),
}
_REGULAR_PRIORITIES = frozenset({Priority.P2, Priority.P3})


def allowed_priorities(note_type: NoteType) -> frozenset[Priority]:
    """``core`` is P0, ``hub`` is P1, every other type is P2 or P3."""
    return _PRIORITIES_BY_TYPE.get(note_type, _REGULAR_PRIORITIES)


def validate_note(note: Note, vocabulary: Vocabulary, policy: SeverityPolicy) -> ValidationReport:
    """Every per-note rule violation (fields, body budget, secret scan), with severities from ``policy``."""
    return policy.report(note_findings(note, vocabulary) + body_budget_findings(note) + scan_note(note))


def note_findings(note: Note, vocabulary: Vocabulary) -> tuple[Finding, ...]:
    """Per-note rules except the body budget. Type is authoritative: rules key on it, not on priority."""
    found: list[Finding] = []

    def add(code: ViolationCode, field: str | None, message: str) -> None:
        found.append(Finding(code=code, field=field, message=message))

    title = note.title.strip()
    if not title:
        add(ViolationCode.TITLE_BLANK, "title", "title is blank")
    elif len(title) > MAX_TITLE_LENGTH:
        add(ViolationCode.TITLE_TOO_LONG, "title", f"longer than {MAX_TITLE_LENGTH} characters")
    if "\n" in note.title or "\r" in note.title:
        add(ViolationCode.TITLE_MULTILINE, "title", "title must be a single line")

    for field_name, text in (("title", note.title), ("summary", note.summary)):
        if "[[" in text:
            add(
                ViolationCode.WIKILINK_IN_METADATA,
                field_name,
                f"{field_name} may not contain '[[' (it is served without link redaction)",
            )

    if not note.lang.strip():
        add(ViolationCode.LANG_BLANK, "lang", "lang is blank")

    if not vocabulary.has_type(note.type):
        add(ViolationCode.UNKNOWN_TYPE, "type", "type is not in schema/types.yaml")
    if not vocabulary.has_area(note.area):
        add(ViolationCode.UNKNOWN_AREA, "area", "area is not in schema/areas.yaml")

    summary_length = len(note.summary.strip())
    if not SUMMARY_MIN_LENGTH <= summary_length <= SUMMARY_MAX_LENGTH:
        add(
            ViolationCode.SUMMARY_LENGTH,
            "summary",
            f"{summary_length} characters; must be {SUMMARY_MIN_LENGTH}-{SUMMARY_MAX_LENGTH}",
        )

    allowed = allowed_priorities(note.type)
    if note.priority not in allowed:
        expected = " or ".join(sorted(p.value for p in allowed))
        add(ViolationCode.PRIORITY_TYPE_MISMATCH, "priority", f"this type requires priority {expected}")
    if (note.type == CORE or note.priority is Priority.P0) and note.sensitivity is Sensitivity.RESTRICTED:
        add(ViolationCode.CORE_RESTRICTED, "sensitivity", "core (P0) notes cannot be restricted")

    if note.type == CORE:
        if note.hub is not None:
            add(ViolationCode.HUB_NOT_ALLOWED, "hub", "core notes do not belong to a hub")
    elif note.hub is None and not note.is_hub:
        add(ViolationCode.HUB_REQUIRED, "hub", "required for notes other than core notes and hubs")
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
        add(ViolationCode.DUPLICATE_RELATED, "related", "duplicate ids")

    if note.updated < note.created:
        add(ViolationCode.DATES_OUT_OF_ORDER, "updated", "updated is before created")

    return tuple(found)
