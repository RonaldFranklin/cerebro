"""``AuthenticateToken``: turn a presented token secret into an ``AccessContext``."""

import hmac

from cerebro.application.dto import AuthenticateTokenRequest
from cerebro.application.errors import AuthenticationFailed
from cerebro.application.ports.system import Clock
from cerebro.application.ports.tokens import TokenStore
from cerebro.application.token_support import hash_token_secret
from cerebro.domain.access import AccessContext
from cerebro.domain.errors import DomainError

MAX_SECRET_LENGTH = 512
"""Longer presented secrets cannot be genuine (generated ones are ~43 characters): refused before hashing."""


class AuthenticateToken:
    """The one place a secret becomes an identity. Used by every inbound adapter, before any other use case.

    * The secret is hashed (SHA-256) and the token is looked up **by hash**; the stored and the computed digests
      are then compared with ``hmac.compare_digest``. The plaintext is never stored, logged or put in an error.
    * Unknown, malformed, revoked and expired tokens all raise the same ``AuthenticationFailed``.
    * The ceiling is ``min(listener, token)`` (``AgentToken.to_context``). The listener comes from the socket the
      request arrived on, passed by the adapter in the request; nothing here reads headers or payloads.
    * This is not an owner context: the owner authenticates differently (Phase 5 login) and uses
      ``AccessContext.for_owner``.
    """

    def __init__(self, tokens: TokenStore, clock: Clock) -> None:
        self._tokens = tokens
        self._clock = clock

    def execute(self, request: AuthenticateTokenRequest) -> AccessContext:
        secret: object = request.secret  # runtime guard: callers may hold untyped data
        if not isinstance(secret, str) or not secret or len(secret) > MAX_SECRET_LENGTH:
            raise AuthenticationFailed
        try:
            presented = hash_token_secret(secret)
        except UnicodeEncodeError:  # e.g. a lone surrogate: must fail like any other bad secret
            presented = None  # raised below, outside the handler: ``__context__`` would hold the secret
        if presented is None:
            raise AuthenticationFailed
        token = self._tokens.find_by_hash(presented)
        if token is None or not hmac.compare_digest(token.token_hash.hexdigest.encode(), presented.hexdigest.encode()):
            raise AuthenticationFailed
        try:
            return token.to_context(request.listener, self._clock.now())
        except DomainError:
            pass
        raise AuthenticationFailed
