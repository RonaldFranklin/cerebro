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
    """A note type, e.g. ``runbook``. Allowed values are the built-in types plus those in ``schema/types.yaml``."""

    value: str

    def __post_init__(self) -> None:
        if not is_slug(self.value):
            raise InvalidValueError("type", f"{self.value!r} is not a kebab-case string")

    def __str__(self) -> str:
        return self.value


CORE = NoteType("core")
HUB = NoteType("hub")

BUILTIN_NOTE_TYPES: frozenset[NoteType] = frozenset({CORE, HUB})
"""Types the domain rules depend on (P0 core notes, hubs). Always allowed; not listed in ``schema/types.yaml``."""

INITIAL_USER_NOTE_TYPES: frozenset[NoteType] = frozenset(
    NoteType(name) for name in ("concept", "guide", "runbook", "reference", "config", "decision", "project")
)
"""User types written to ``schema/types.yaml`` when a content repository is initialised."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Vocabulary:
    """The areas and note types a content repository allows.

    ``types`` is given the user types from ``schema/types.yaml``; the built-in ``core`` and ``hub`` types are
    always added. Declaring them in the schema as well is accepted and does not duplicate them.
    """

    areas: frozenset[Area]
    types: frozenset[NoteType]

    def __post_init__(self) -> None:
        object.__setattr__(self, "types", frozenset(self.types) | BUILTIN_NOTE_TYPES)

    @property
    def user_types(self) -> frozenset[NoteType]:
        """The allowed types other than the built-in ones."""
        return self.types - BUILTIN_NOTE_TYPES

    def has_area(self, area: Area) -> bool:
        return area in self.areas

    def has_type(self, note_type: NoteType) -> bool:
        return note_type in self.types
