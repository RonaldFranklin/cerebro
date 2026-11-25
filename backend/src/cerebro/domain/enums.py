"""Closed sets of values defined by the domain model: priority, sensitivity and status."""

from enum import StrEnum
from typing import Self

from cerebro.domain.errors import InvalidValueError


class _OrderedStrEnum(StrEnum):
    """String enum ordered by declaration order.

    Comparing with anything that is not a member of the same enum raises ``TypeError``, including plain
    strings: falling back to ``str`` ordering would silently sort alphabetically (e.g. a raw ``"public"``
    would rank above ``internal``).
    """

    def _ranks(self, other: object) -> tuple[int, int]:
        if type(other) is not type(self):
            raise TypeError(f"cannot order {type(self).__name__} against {type(other).__name__}")
        members = list(type(self))
        return members.index(self), members.index(other)

    def __lt__(self, other: object) -> bool:
        mine, theirs = self._ranks(other)
        return mine < theirs

    def __le__(self, other: object) -> bool:
        mine, theirs = self._ranks(other)
        return mine <= theirs

    def __gt__(self, other: object) -> bool:
        mine, theirs = self._ranks(other)
        return mine > theirs

    def __ge__(self, other: object) -> bool:
        mine, theirs = self._ranks(other)
        return mine >= theirs


class _UnorderedStrEnum(StrEnum):
    """String enum without an order: ``<``, ``<=``, ``>`` and ``>=`` raise ``TypeError``."""

    def _no_order(self, other: object) -> bool:
        raise TypeError(f"{type(self).__name__} has no order")

    __lt__ = __le__ = __gt__ = __ge__ = _no_order


def _parse[E: StrEnum](enum: type[E], value: str, field: str) -> E:
    if not isinstance(value, str):
        raise InvalidValueError(field, f"expected a string, got {type(value).__name__}")
    try:
        return enum(value)
    except ValueError:
        allowed = ", ".join(member.value for member in enum)
        raise InvalidValueError(field, f"{value!r} is not one of: {allowed}") from None


class Priority(_OrderedStrEnum):
    """Context budget level. ``P0`` is the highest priority, so ``P0 < P1 < P2 < P3``."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"

    @classmethod
    def parse(cls, value: str) -> Self:
        return _parse(cls, value, "priority")


class Sensitivity(_OrderedStrEnum):
    """Who may see a note, ordered ``public < internal < restricted``."""

    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"

    @classmethod
    def parse(cls, value: str) -> Self:
        return _parse(cls, value, "sensitivity")


class Status(_UnorderedStrEnum):
    """Lifecycle of the knowledge in a note. Only ``superseded`` and ``archived`` are history."""

    CURRENT = "current"
    PROPOSED = "proposed"
    APPLIED = "applied"
    VERIFIED = "verified"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"

    @classmethod
    def parse(cls, value: str) -> Self:
        return _parse(cls, value, "status")

    @property
    def is_current(self) -> bool:
        """Whether notes with this status are served as current knowledge (without ``include_history``)."""
        return self not in (Status.SUPERSEDED, Status.ARCHIVED)
