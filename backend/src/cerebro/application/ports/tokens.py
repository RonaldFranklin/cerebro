"""``TokenStore``: agent tokens, stored by hash only."""

from typing import Protocol

from cerebro.domain.agent_token import AgentToken


class TokenStore(Protocol):
    def add(self, token: AgentToken) -> None:
        """Store a new token. Raises ``AlreadyExists`` if the id or the hash is taken."""
        ...

    def get(self, token_id: str) -> AgentToken | None: ...

    def find_by_hash(self, token_hash: str) -> AgentToken | None:
        """The token with this SHA-256 hash, **including revoked or expired ones**: the caller checks
        ``is_valid`` (or ``to_context``), so a revoked token is told apart from an unknown one in the audit.
        """
        ...

    def save(self, token: AgentToken) -> None:
        """Replace a stored token (e.g. after ``revoke``). Raises ``TokenNotFound`` if unknown."""
        ...

    def all(self) -> tuple[AgentToken, ...]:
        """Every token, ordered by ``created_at`` then id."""
        ...
