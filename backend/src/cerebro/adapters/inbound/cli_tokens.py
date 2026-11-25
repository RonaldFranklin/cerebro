"""Owner CLI output for agent credentials."""

import json

from cerebro.application.dto import (
    AgentTokenSummary,
    IssueAgentTokenRequest,
    IssuedToken,
    ListAgentTokensRequest,
    RevokeAgentTokenRequest,
    RevokedToken,
)
from cerebro.application.use_case import ContextUseCase
from cerebro.domain.access import AccessContext


def run_token_command(
    request: IssueAgentTokenRequest | ListAgentTokensRequest | RevokeAgentTokenRequest,
    ctx: AccessContext,
    *,
    issue: ContextUseCase[IssueAgentTokenRequest, IssuedToken],
    list_tokens: ContextUseCase[ListAgentTokensRequest, tuple[AgentTokenSummary, ...]],
    revoke: ContextUseCase[RevokeAgentTokenRequest, RevokedToken],
) -> int:
    match request:
        case IssueAgentTokenRequest():
            print(issue.execute(request, ctx).secret)
        case ListAgentTokensRequest():
            rows = [
                {
                    "token_id": token.token_id,
                    "agent": token.agent,
                    "ceiling": token.ceiling.level.value,
                    "scopes": sorted(scope.value for scope in token.scopes),
                    "created_at": token.created_at.isoformat(),
                    "expires_at": token.expires_at.isoformat() if token.expires_at else None,
                    "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
                }
                for token in list_tokens.execute(request, ctx)
            ]
            print(json.dumps(rows, indent=2))
        case RevokeAgentTokenRequest():
            outcome = revoke.execute(request, ctx)
            print(
                json.dumps(
                    {
                        "token_id": outcome.token_id,
                        "revoked_at": outcome.revoked_at.isoformat(),
                        "already_revoked": outcome.already_revoked,
                    }
                )
            )
    return 0
