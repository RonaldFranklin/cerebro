"""Access control primitives: ceilings, visibility, principals, scopes and the per-request context.

Rules (``docs/domain-model.md`` section 4, ADR-0005):

* ``effective_ceiling = min(listener_ceiling, token_ceiling)``. Both inputs are mandatory; there is no default,
  so a caller can never fall back to a permissive ceiling by forgetting one.
* A note above the ceiling, or in history without ``include_history``, is **invisible**. ``is_visible`` only
  answers yes/no; callers must treat "not visible" exactly like "does not exist" (no existence leak).
* Everything here fails closed: a value of an unexpected type is denied, never allowed.

The listener is chosen by the socket a request arrived on. This module never sees request headers.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Self

from cerebro.domain.enums import Sensitivity, Status, _parse
from cerebro.domain.errors import InvalidValueError

_AGENT_NAME: Final = re.compile(r"[a-z0-9][a-z0-9._-]*(?:@[a-z0-9][a-z0-9._-]*)?")
AGENT_NAME_MAX_LENGTH: Final = 64
# The owner is recorded as ``owner`` in commit trailers (``Approved-by: owner``); an agent must never be able to
# appear under that name in the audit trail, with or without a host part.
_RESERVED_AGENT_NAMES: Final = frozenset({"owner"})


@dataclass(frozen=True, slots=True)
class AccessCeiling:
    """The highest sensitivity a caller may see."""

    level: Sensitivity

    def __post_init__(self) -> None:
        if not isinstance(self.level, Sensitivity):
            raise InvalidValueError("ceiling", f"expected a Sensitivity, got {type(self.level).__name__}")

    def allows(self, sensitivity: Sensitivity) -> bool:
        """Whether a note of this sensitivity is within the ceiling; a non-``Sensitivity`` is denied."""
        value: object = sensitivity  # runtime guard: callers may hold untyped data
        if not isinstance(value, Sensitivity):
            return False
        return value <= self.level


def _require_ceiling(value: object, field: str) -> AccessCeiling:
    if not isinstance(value, AccessCeiling):
        raise InvalidValueError(field, f"expected an AccessCeiling, got {type(value).__name__}")
    return value


def effective_ceiling(listener: AccessCeiling, token: AccessCeiling) -> AccessCeiling:
    """The lower of the listener ceiling and the token ceiling. A token can only lower what the listener allows."""
    listener = _require_ceiling(listener, "listener")
    token = _require_ceiling(token, "token")
    return AccessCeiling(min(listener.level, token.level))


def is_visible(
    sensitivity: Sensitivity,
    status: Status,
    ceiling: AccessCeiling,
    *,
    include_history: bool = False,
) -> bool:
    """Whether a note may be served under ``ceiling``.

    ``superseded`` and ``archived`` notes are history: hidden unless ``include_history`` is exactly ``True``.
    ``include_history`` never raises the ceiling. Wrongly typed arguments are denied.
    """
    ceiling_value: object = ceiling  # runtime guards: callers may hold untyped data
    status_value: object = status
    if not isinstance(ceiling_value, AccessCeiling) or not isinstance(status_value, Status):
        return False
    if not ceiling_value.allows(sensitivity):
        return False
    return status_value.is_current or include_history is True


class Scope(StrEnum):
    """What an agent token may do. Owner-only actions are not scopes: they require the ``Owner`` principal."""

    READ = "read"
    PROPOSE = "propose"

    @classmethod
    def parse(cls, value: str) -> Self:
        return _parse(cls, value, "scope")


def validate_agent_name(name: object) -> str:
    """Return ``name`` if it is a valid agent name (``claude-code@workstation``), else raise."""
    if not isinstance(name, str):
        raise InvalidValueError("agent", f"expected a string, got {type(name).__name__}")
    if len(name) > AGENT_NAME_MAX_LENGTH or _AGENT_NAME.fullmatch(name) is None:
        raise InvalidValueError(
            "agent",
            f"must be lowercase letters, digits, '.', '_', '-' and at most one '@' (max {AGENT_NAME_MAX_LENGTH})",
        )
    if name.partition("@")[0] in _RESERVED_AGENT_NAMES:
        raise InvalidValueError("agent", "this name is reserved for the owner")
    return name


@dataclass(frozen=True, slots=True)
class Agent:
    """An AI agent identified by its token name. The name becomes ``source.agent`` on proposals."""

    name: str

    def __post_init__(self) -> None:
        validate_agent_name(self.name)


@dataclass(frozen=True, slots=True)
class Owner:
    """The human owner. Has every scope, but is still limited by the ceiling of the listener."""


type Principal = Agent | Owner


@dataclass(frozen=True, slots=True, kw_only=True)
class AccessContext:
    """Who is asking and what they may do, built once per request by the inbound adapter.

    ``ceiling`` is the **already computed** effective ceiling (``effective_ceiling(listener, token)``); use
    cases never recompute or widen it. The owner holds every scope regardless of ``scopes``, and is limited by
    the listener ceiling only (use ``for_owner``).
    """

    principal: Principal
    ceiling: AccessCeiling
    scopes: frozenset[Scope]

    def __post_init__(self) -> None:
        if not isinstance(self.principal, Agent | Owner):
            raise InvalidValueError("principal", f"expected Agent or Owner, got {type(self.principal).__name__}")
        _require_ceiling(self.ceiling, "ceiling")
        if not isinstance(self.scopes, frozenset) or not all(isinstance(s, Scope) for s in self.scopes):
            raise InvalidValueError("scopes", "expected a frozenset of Scope")
        # A frozenset subclass could override ``__contains__``; keep a plain copy so ``can`` sees the real members.
        object.__setattr__(self, "scopes", frozenset(self.scopes))

    @classmethod
    def for_owner(cls, listener: AccessCeiling) -> Self:
        """Context of the owner: all scopes, ceiling limited by the listener."""
        return cls(principal=Owner(), ceiling=_require_ceiling(listener, "listener"), scopes=frozenset(Scope))

    @property
    def is_owner(self) -> bool:
        return isinstance(self.principal, Owner)

    def can(self, scope: Scope) -> bool:
        """Whether the principal holds ``scope``. Anything that is not a ``Scope`` is denied."""
        value: object = scope  # runtime guard: callers may hold untyped data
        if not isinstance(value, Scope):
            return False
        return self.is_owner or value in self.scopes
