"""``AgentToken``: the stored record of an agent credential. Never holds the plaintext secret."""

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Final, Self

from cerebro.domain.access import AccessCeiling, AccessContext, Agent, Scope, effective_ceiling, validate_agent_name
from cerebro.domain.errors import DomainError, InvalidValueError
from cerebro.domain.token_hash import TokenHash

_TOKEN_ID: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")


class TokenNotValidError(DomainError):
    """The token is expired or revoked and must not authenticate anything."""


def _require_aware(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidValueError(field_name, "expected a timezone-aware datetime")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentToken:
    """One credential per agent: who, how high it may see, what it may do, and when it stops working.

    Only the SHA-256 digest of the secret is kept (``token_hash``, a ``TokenHash`` that shows ``<redacted>`` in
    ``repr``, ``str`` and ``asdict``); the plaintext is shown once at issue time and never enters the domain.
    ``token_hash`` is excluded from ``__eq__`` and ``__hash__``: tokens compare by their other fields, and the
    hash is compared explicitly (constant-time) by whoever looks a token up.
    """

    id: str
    agent: str
    ceiling: AccessCeiling
    scopes: frozenset[Scope]
    token_hash: TokenHash = field(compare=False)
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or _TOKEN_ID.fullmatch(self.id) is None:
            raise InvalidValueError("id", "must be 1-64 characters of letters, digits, '_' or '-'")
        validate_agent_name(self.agent)
        if not isinstance(self.ceiling, AccessCeiling):
            raise InvalidValueError("ceiling", "expected an AccessCeiling")
        if (
            not isinstance(self.scopes, frozenset)
            or not self.scopes
            or not all(isinstance(s, Scope) for s in self.scopes)
        ):
            raise InvalidValueError("scopes", "expected a non-empty frozenset of Scope")
        object.__setattr__(self, "scopes", frozenset(self.scopes))  # plain copy, see AccessContext
        if not isinstance(self.token_hash, TokenHash):
            raise InvalidValueError("token_hash", "expected a TokenHash")
        _require_aware(self.created_at, "created_at")
        if self.expires_at is not None and _require_aware(self.expires_at, "expires_at") <= self.created_at:
            raise InvalidValueError("expires_at", "must be after created_at")
        if self.revoked_at is not None and _require_aware(self.revoked_at, "revoked_at") < self.created_at:
            raise InvalidValueError("revoked_at", "must not precede created_at")

    def is_valid(self, now: datetime) -> bool:
        """Not revoked and not expired at ``now`` (a timezone-aware time; a naive one raises)."""
        _require_aware(now, "now")
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or now < self.expires_at

    def revoke(self, now: datetime) -> Self:
        """A revoked copy. Revoking again keeps the original revocation time."""
        _require_aware(now, "now")
        if self.revoked_at is not None:
            return self
        return replace(self, revoked_at=max(now, self.created_at))

    def to_context(self, listener: AccessCeiling, now: datetime) -> AccessContext:
        """Build the request context for this token on a listener; raises ``TokenNotValidError`` if unusable."""
        if not self.is_valid(now):
            raise TokenNotValidError("token is expired or revoked")
        return AccessContext(
            principal=Agent(self.agent),
            ceiling=effective_ceiling(listener, self.ceiling),
            scopes=self.scopes,
        )
