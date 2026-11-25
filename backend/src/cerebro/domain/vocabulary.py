"""Controlled vocabularies: areas and note types come from the content repository (``schema/*.yaml``)."""

from dataclasses import dataclass

from cerebro.domain.errors import InvalidValueError
from cerebro.domain.slug import is_slug


@dataclass(frozen=True, slots=True, order=True)
class Area:
    """An area of knowledge, e.g. ``infrastructure``. Allowed values are listed in ``schema/areas.yaml``."""

    value: str

    def __post_init__(self) -> None:
        if not is_slug(self.value):
            raise InvalidValueError("area", f"{self.value!r} is not a kebab-case string")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class NoteType:
    """A note type, e.g. ``runbook``. Allowed values are listed in ``schema/types.yaml``."""

    value: str

    def __post_init__(self) -> None:
        if not is_slug(self.value):
            raise InvalidValueError("type", f"{self.value!r} is not a kebab-case string")

    def __str__(self) -> str:
        return self.value


CORE = NoteType("core")
HUB = NoteType("hub")

DEFAULT_NOTE_TYPES: frozenset[NoteType] = frozenset(
    NoteType(name)
    for name in ("core", "hub", "concept", "guide", "runbook", "reference", "config", "decision", "project")
)

_REQUIRED_TYPES = frozenset({CORE, HUB})


@dataclass(frozen=True, slots=True, kw_only=True)
class Vocabulary:
    """The areas and note types a content repository allows.

    ``core`` and ``hub`` are always required because domain rules (P0 notes, hubs) refer to them.
    """

    areas: frozenset[Area]
    types: frozenset[NoteType]

    def __post_init__(self) -> None:
        missing = _REQUIRED_TYPES - self.types
        if missing:
            names = ", ".join(sorted(t.value for t in missing))
            raise InvalidValueError("types", f"missing required note types: {names}")

    def has_area(self, area: Area) -> bool:
        return area in self.areas

    def has_type(self, note_type: NoteType) -> bool:
        return note_type in self.types
