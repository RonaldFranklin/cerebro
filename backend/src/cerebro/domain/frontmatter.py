"""Boundary between raw frontmatter (plain data, as a YAML loader returns it) and the typed ``Note``.

The domain never parses YAML. Adapters hand over ``Mapping[str, object]`` plus the body and get back either a
``Note`` or a ``ValidationReport`` listing every structural problem at once.
"""

import re
from collections.abc import Callable, Mapping
from datetime import date
from typing import Final

from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.note import Note, NoteId
from cerebro.domain.report import ValidationReport, Violation, ViolationCode
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


class _FieldFormatError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def parse_note(raw: Mapping[str, object], body: str) -> Note | ValidationReport:
    """Build a ``Note`` from raw frontmatter, or report every structural problem.

    Structural problems are missing required keys, unknown keys and values of the wrong type or format.
    Content rules (lengths, budgets, cross-field rules) are checked separately by ``validate_note``.
    """
    violations: list[Violation] = []
    values: dict[str, object] = {}

    # Annotated as ``object``: YAML can produce non-string keys whatever the static type says.
    keys: list[object] = list(raw)
    for key in keys:
        if not isinstance(key, str) or key not in FIELD_ORDER:
            violations.append(Violation(code=ViolationCode.UNKNOWN_FIELD, field=str(key), message="unknown field"))

    for field in FIELD_ORDER:
        value = raw.get(field)
        if value is None:
            if field in REQUIRED_FIELDS:
                violations.append(Violation(code=ViolationCode.MISSING_FIELD, field=field, message="required"))
            else:
                values[field] = _DEFAULTS[field]
            continue
        try:
            values[field] = _PARSERS[field](value)
        except (_FieldFormatError, InvalidValueError) as error:
            violations.append(Violation(code=ViolationCode.INVALID_FIELD, field=field, message=error.message))

    body_value: object = body
    if not isinstance(body_value, str):
        violations.append(Violation(code=ViolationCode.INVALID_FIELD, field="body", message="body must be text"))

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
    if not isinstance(value, str):
        raise _FieldFormatError(f"expected text, got {type(value).__name__}")
    return value


def _non_empty_text(value: object) -> str:
    text = _text(value)
    if not text:
        raise _FieldFormatError("must not be empty")
    return text


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
    "lang": _non_empty_text,
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
