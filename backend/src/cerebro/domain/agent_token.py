"""``AgentToken``: the stored record of an agent credential. Never holds the plaintext secret."""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Final, Self

from cerebro.domain.access import (
    AccessCeiling,
    AccessContext,
    Agent,
    Scope,
    effective_ceiling,
    validate_agent_name,
    validate_credential_scopes,
)
from cerebro.domain.access import validate_token_id as validate_token_id
from cerebro.domain.errors import DomainError, InvalidValueError
from cerebro.domain.token_hash import TokenHash

CLOCK_SKEW_TOLERANCE: Final = timedelta(minutes=5)
"""How far in the future a token's ``created_at`` may be, compared with the verifier's clock (clock skew between
the machine that issued it and the one that checks it). Beyond that the token is not valid: a record dated in the
future is either forged or the clock is wrong, and a wrong clock must not stretch a token's life."""


class TokenNotValidError(DomainError):
    """The token is expired, revoked or not yet valid and must not authenticate anything."""


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
        validate_token_id(self.id)
        validate_agent_name(self.agent)
        if not isinstance(self.ceiling, AccessCeiling):
            raise InvalidValueError("ceiling", "expected an AccessCeiling")
        object.__setattr__(self, "scopes", validate_credential_scopes(self.scopes))
        if type(self.token_hash) is not TokenHash:
            raise InvalidValueError("token_hash", "expected a TokenHash")
        _require_aware(self.created_at, "created_at")
        if self.expires_at is not None and _require_aware(self.expires_at, "expires_at") <= self.created_at:
            raise InvalidValueError("expires_at", "must be after created_at")
        if self.revoked_at is not None and _require_aware(self.revoked_at, "revoked_at") < self.created_at:
            raise InvalidValueError("revoked_at", "must not precede created_at")

    def is_valid(self, now: datetime) -> bool:
        """Not revoked, not expired and not created in the future (beyond ``CLOCK_SKEW_TOLERANCE``) at ``now``
        (a timezone-aware time; a naive one raises)."""
        _require_aware(now, "now")
        if self.revoked_at is not None or self.created_at > now + CLOCK_SKEW_TOLERANCE:
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
            raise TokenNotValidError("token is expired, revoked or not yet valid")
        return AccessContext(
            principal=Agent(self.agent),
            token_id=self.id,
            ceiling=effective_ceiling(listener, self.ceiling),
            scopes=self.scopes,
        )
