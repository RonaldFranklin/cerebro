"""Boundary between raw frontmatter (plain data, as a YAML loader returns it) and the typed ``Note``.

The domain never parses YAML. Adapters hand over what the YAML loader returned (normally a mapping) plus the
body and get back either a ``Note`` or a ``ValidationReport`` listing every structural problem at once.
"""

import re
from collections.abc import Callable, Mapping
from datetime import date
from typing import Final

from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.note import Note, NoteId
from cerebro.domain.report import Severity, ValidationReport, Violation, ViolationCode
from cerebro.domain.secrets import scan_text
from cerebro.domain.vocabulary import Area, NoteType

FIELD_ORDER: Final = (
    "id",
    "title",
    "type",
    "area",
    "priority",
    "sensitivity",
    "status",
    "summary",
    "hub",
    "related",
    "superseded_by",
    "lang",
    "created",
    "updated",
    "review_by",
)
"""Canonical order of frontmatter keys, used when writing notes so diffs stay stable."""

REQUIRED_FIELDS: Final = frozenset(
    {"id", "title", "type", "area", "priority", "sensitivity", "status", "summary", "created", "updated"}
)
DEFAULT_LANG: Final = "en"

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_REDACTED_VALUE: Final = "invalid value that looks like a secret (not shown); notes must never contain secrets"
_REDACTED_KEY: Final = "unknown frontmatter key that looks like a secret (not shown); notes must never contain secrets"


class _FieldFormatError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def parse_note(raw: object, body: object) -> Note | ValidationReport:
    """Build a ``Note`` from raw frontmatter, or report every structural problem (always errors).

    ``raw`` is whatever the YAML loader returned: a mapping for a well-formed note, but ``None`` for empty
    frontmatter or a list/scalar for malformed files, which are reported instead of raising.
    Structural problems are missing required keys, unknown keys and values of the wrong type or format.
    Content rules (lengths, budgets, cross-field rules) are checked separately by ``validate_note``.
    """
    violations: list[Violation] = []
    values: dict[str, object] = {}

    def error(code: ViolationCode, field: str | None, message: str) -> None:
        violations.append(Violation(code=code, field=field, message=message, severity=Severity.ERROR))

    if not isinstance(raw, Mapping):
        error(ViolationCode.INVALID_FIELD, None, f"frontmatter must be a mapping, got {type(raw).__name__}")
        raw = {}

    for key in raw:
        if not isinstance(key, str) or key not in FIELD_ORDER:
            if scan_text(str(key), field="frontmatter"):
                error(ViolationCode.SECRET_DETECTED, None, _REDACTED_KEY)
            else:
                error(ViolationCode.UNKNOWN_FIELD, str(key), "unknown field")

    for field in FIELD_ORDER:
        value = raw.get(field)
        if value is None:
            if field in REQUIRED_FIELDS:
                error(ViolationCode.MISSING_FIELD, field, "required")
            else:
                values[field] = _DEFAULTS[field]
            continue
        try:
            values[field] = _PARSERS[field](value)
        except (_FieldFormatError, InvalidValueError) as exc:
            # Messages may quote the rejected value: never echo one that looks like a secret.
            if scan_text(exc.message, field=field):
                error(ViolationCode.SECRET_DETECTED, field, _REDACTED_VALUE)
            else:
                error(ViolationCode.INVALID_FIELD, field, exc.message)

    if not isinstance(body, str):
        error(ViolationCode.INVALID_FIELD, "body", "body must be text")

    if violations:
        return ValidationReport(tuple(violations))
    return Note(body=body, **values)  # type: ignore[arg-type]


def note_to_raw(note: Note) -> dict[str, object]:
    """Plain-data frontmatter in canonical order; empty optional fields are omitted. Dates stay ``date``."""
    raw: dict[str, object] = {
        "id": note.id.value,
        "title": note.title,
        "type": note.type.value,
        "area": note.area.value,
        "priority": note.priority.value,
        "sensitivity": note.sensitivity.value,
        "status": note.status.value,
        "summary": note.summary,
        "hub": note.hub.value if note.hub else None,
        "related": [r.value for r in note.related] or None,
        "superseded_by": note.superseded_by.value if note.superseded_by else None,
        "lang": note.lang,
        "created": note.created,
        "updated": note.updated,
        "review_by": note.review_by,
    }
    return {key: value for key, value in raw.items() if value is not None}


def _text(value: object) -> str:
    if isinstance(value, (bool, int, float)):
        # YAML 1.1 turns unquoted ``no``, ``on``, ``2026``, ``0x1f`` ... into booleans and numbers.
        raise _FieldFormatError(f"expected text, got {type(value).__name__} {value!r} (quote the value)")
    if not isinstance(value, str):
        raise _FieldFormatError(f"expected text, got {type(value).__name__}")
    return value


def _note_id(value: object) -> NoteId:
    return NoteId(_text(value))


def _note_ids(value: object) -> tuple[NoteId, ...]:
    if not isinstance(value, (list, tuple)):
        raise _FieldFormatError(f"expected a list of ids, got {type(value).__name__}")
    return tuple(_note_id(item) for item in value)


def _date(value: object) -> date:
    # ``datetime`` is a ``date`` subclass; a timestamp is not a valid note date.
    if type(value) is date:
        return value
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise _FieldFormatError(f"expected a date (YYYY-MM-DD), got {value!r}")


_PARSERS: Final[dict[str, Callable[[object], object]]] = {
    "id": _note_id,
    "title": _text,
    "type": lambda v: NoteType(_text(v)),
    "area": lambda v: Area(_text(v)),
    "priority": lambda v: Priority.parse(_text(v)),
    "sensitivity": lambda v: Sensitivity.parse(_text(v)),
    "status": lambda v: Status.parse(_text(v)),
    "summary": _text,
    "hub": _note_id,
    "related": _note_ids,
    "superseded_by": _note_id,
    "lang": _text,
    "created": _date,
    "updated": _date,
    "review_by": _date,
}

_DEFAULTS: Final[dict[str, object]] = {
    "hub": None,
    "related": (),
    "superseded_by": None,
    "lang": DEFAULT_LANG,
    "review_by": None,
}
