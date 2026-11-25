"""``RevokeAgentToken``: stop a credential from working."""

from cerebro.application.access import require_owner
from cerebro.application.dto import RevokeAgentTokenRequest, RevokedToken
from cerebro.application.errors import TokenNotFound
from cerebro.application.ports.system import Clock
from cerebro.application.ports.tokens import TokenStore
from cerebro.domain.access import AccessContext


class RevokeAgentToken:
    """Owner only (checked before the store is read, so a non-owner learns nothing about token ids).

    Idempotent: revoking a revoked token succeeds, keeps the original revocation time and writes nothing. An unknown
    id raises ``TokenNotFound`` (to the owner only), whose message holds only the id the owner typed.
    """

    def __init__(self, tokens: TokenStore, clock: Clock) -> None:
        self._tokens = tokens
        self._clock = clock

    def execute(self, request: RevokeAgentTokenRequest, ctx: AccessContext) -> RevokedToken:
        require_owner(ctx, "revoke tokens")
        token = self._tokens.get(request.token_id)
        if token is None:
            raise TokenNotFound(request.token_id)
        if token.revoked_at is not None:
            return RevokedToken(token_id=token.id, revoked_at=token.revoked_at, already_revoked=True)
        now = self._clock.now()
        revoked = token.revoke(now)
        self._tokens.save(revoked)
        return RevokedToken(token_id=revoked.id, revoked_at=revoked.revoked_at or now, already_revoked=False)
