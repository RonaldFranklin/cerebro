"""``IssueAgentToken``: create a credential for an agent."""

from cerebro.application.access import require_owner
from cerebro.application.dto import IssueAgentTokenRequest, IssuedToken
from cerebro.application.errors import PermissionDenied
from cerebro.application.ports.system import Clock, IdGenerator
from cerebro.application.ports.tokens import TokenStore
from cerebro.application.token_support import MIN_SECRET_LENGTH, hash_token_secret
from cerebro.domain.access import AccessContext
from cerebro.domain.agent_token import AgentToken
from cerebro.domain.errors import InvalidValueError


class IssueAgentToken:
    """Owner only. The secret comes from ``IdGenerator.new_token_secret``; only its SHA-256 hash is stored. The
    plaintext is returned once, in ``IssuedToken`` (excluded from its ``repr``), and cannot be recovered later.

    The agent name (the reserved name ``owner`` is refused), the scopes and the expiry are validated by
    ``AgentToken``. The ceiling of the new token cannot exceed the owner's own ceiling on this listener, so a
    low-ceiling listener cannot mint a token that sees more than the listener does.
    """

    def __init__(self, tokens: TokenStore, clock: Clock, ids: IdGenerator) -> None:
        self._tokens = tokens
        self._clock = clock
        self._ids = ids

    def execute(self, request: IssueAgentTokenRequest, ctx: AccessContext) -> IssuedToken:
        require_owner(ctx, "issue tokens")
        if request.ceiling.level > ctx.ceiling.level:
            raise PermissionDenied("issue a token above your own ceiling")
        secret = self._ids.new_token_secret()
        if not isinstance(secret, str) or len(secret) < MIN_SECRET_LENGTH:
            raise InvalidValueError("secret", "the generated secret is too short")  # never echo it
        now = self._clock.now()
        token = AgentToken(
            id=self._ids.new_token_id(),
            agent=request.agent,
            ceiling=request.ceiling,
            scopes=request.scopes,
            token_hash=hash_token_secret(secret),
            created_at=now,
            expires_at=request.expires_at,
        )
        self._tokens.add(token)
        return IssuedToken(
            token_id=token.id,
            agent=token.agent,
            ceiling=token.ceiling,
            scopes=token.scopes,
            created_at=token.created_at,
            expires_at=token.expires_at,
            secret=secret,
        )
