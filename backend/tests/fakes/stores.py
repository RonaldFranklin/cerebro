"""In-memory ``ProposalStore`` and ``TokenStore``, and a scriptable ``Curator``."""

from cerebro.application.errors import (
    AlreadyExists,
    TokenNotFound,
)
from cerebro.domain.agent_token import AgentToken
from cerebro.domain.token_hash import TokenHash


class InMemoryTokenStore:
    def __init__(self) -> None:
        self._items: dict[str, AgentToken] = {}

    def add(self, token: AgentToken) -> None:
        if token.id in self._items:
            raise AlreadyExists("token", token.id)
        if self.find_by_hash(token.token_hash) is not None:
            raise AlreadyExists("token hash", token.id)
        self._items[token.id] = token

    def get(self, token_id: str) -> AgentToken | None:
        return self._items.get(token_id)

    def find_by_hash(self, token_hash: TokenHash) -> AgentToken | None:
        return next((t for t in self._items.values() if t.token_hash == token_hash), None)

    def save(self, token: AgentToken) -> None:
        if token.id not in self._items:
            raise TokenNotFound(token.id)
        self._items[token.id] = token

    def all(self) -> tuple[AgentToken, ...]:
        return tuple(sorted(self._items.values(), key=lambda t: (t.created_at, t.id)))
