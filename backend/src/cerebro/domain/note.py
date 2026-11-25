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
        if len(self.value) > MAX_NOTE_ID_LENGTH:
            raise InvalidValueError("id", f"longer than {MAX_NOTE_ID_LENGTH} characters")
        if not is_slug(self.value):
            raise InvalidValueError("id", f"{self.value!r} is not kebab-case")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, kw_only=True)
class Note:
    """A note: typed frontmatter plus Markdown body.

    Construction only guarantees that every field has the right type. Content rules (lengths, budgets,
    cross-field and cross-note rules) are checked by validation, which returns all violations at once, so
    an existing note that breaks a rule can still be loaded and reported.
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
        if not isinstance(self.related, tuple):
            raise TypeError("related must be a tuple of NoteId")

    @property
    def is_hub(self) -> bool:
        return self.type == HUB

    @property
    def is_current(self) -> bool:
        return self.status.is_current
