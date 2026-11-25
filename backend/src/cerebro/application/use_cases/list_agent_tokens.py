"""List account credentials for the owner."""

from cerebro.application.access import require_owner
from cerebro.application.dto import AgentTokenSummary, ListAgentTokensRequest
from cerebro.application.ports.tokens import TokenStore
from cerebro.domain.access import AccessContext


class ListAgentTokens:
    def __init__(self, tokens: TokenStore) -> None:
        self._tokens = tokens

    def execute(self, request: ListAgentTokensRequest, ctx: AccessContext) -> tuple[AgentTokenSummary, ...]:
        require_owner(ctx, "list tokens")
        return tuple(
            AgentTokenSummary(
                token_id=token.id,
                agent=token.agent,
                ceiling=token.ceiling,
                scopes=token.scopes,
                created_at=token.created_at,
                expires_at=token.expires_at,
                revoked_at=token.revoked_at,
            )
            for token in self._tokens.all()
        )
