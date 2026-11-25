"""The note entity and its identifier."""

from dataclasses import dataclass
from datetime import date

from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.slug import is_slug
from cerebro.domain.vocabulary import HUB, Area, NoteType

MAX_NOTE_ID_LENGTH = 80


@dataclass(frozen=True, slots=True, order=True)
class NoteId:
    """Kebab-case, unique, immutable note identifier; equals the file name without ``.md``."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise InvalidValueError("id", f"expected a string, got {type(self.value).__name__}")
        if len(self.value) > MAX_NOTE_ID_LENGTH:
            raise InvalidValueError("id", f"longer than {MAX_NOTE_ID_LENGTH} characters")
        if not is_slug(self.value):
            raise InvalidValueError("id", "not kebab-case")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, kw_only=True)
class Note:
    """A note: typed frontmatter plus Markdown body.

    Construction guarantees that every field has the right type (raising ``InvalidValueError`` naming the
    field otherwise); dates must be ``date``, not ``datetime``. Content rules (lengths, budgets, cross-field
    and cross-note rules) are checked by validation, which returns all violations at once, so an existing
    note that breaks a rule can still be loaded and reported.
    """

    id: NoteId
    title: str
    type: NoteType
    area: Area
    priority: Priority
    sensitivity: Sensitivity
    status: Status
    summary: str
    hub: NoteId | None
    related: tuple[NoteId, ...]
    superseded_by: NoteId | None
    lang: str
    created: date
    updated: date
    review_by: date | None
    body: str

    def __post_init__(self) -> None:
        _require(self.id, NoteId, "id")
        _require(self.title, str, "title")
        _require(self.type, NoteType, "type")
        _require(self.area, Area, "area")
        _require(self.priority, Priority, "priority")
        _require(self.sensitivity, Sensitivity, "sensitivity")
        _require(self.status, Status, "status")
        _require(self.summary, str, "summary")
        _require_optional(self.hub, NoteId, "hub")
        if not isinstance(self.related, tuple) or not all(isinstance(i, NoteId) for i in self.related):
            raise InvalidValueError("related", "expected a tuple of NoteId")
        _require_optional(self.superseded_by, NoteId, "superseded_by")
        _require(self.lang, str, "lang")
        _require_date(self.created, "created")
        _require_date(self.updated, "updated")
        if self.review_by is not None:
            _require_date(self.review_by, "review_by")
        _require(self.body, str, "body")

    @property
    def is_hub(self) -> bool:
        return self.type == HUB

    @property
    def is_current(self) -> bool:
        return self.status.is_current


def _require(value: object, expected: type, field: str) -> None:
    if not isinstance(value, expected):
        raise InvalidValueError(field, f"expected {expected.__name__}, got {type(value).__name__}")


def _require_optional(value: object, expected: type, field: str) -> None:
    if value is not None:
        _require(value, expected, field)


def _require_date(value: object, field: str) -> None:
    # ``datetime`` is a subclass of ``date`` but cannot be compared with one, so it is rejected explicitly.
    if type(value) is not date:
        raise InvalidValueError(field, f"expected date, got {type(value).__name__}")
