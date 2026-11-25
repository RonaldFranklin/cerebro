"""Token secrets: hashing shared by ``IssueAgentToken`` and ``AuthenticateToken``."""

import hashlib
from typing import Final

from cerebro.domain.token_hash import TokenHash

MIN_SECRET_LENGTH: Final = 32
"""Shortest acceptable generated secret (characters); a shorter one means the ``IdGenerator`` is broken."""


def hash_token_secret(secret: str) -> TokenHash:
    """SHA-256 digest of a token secret. Secrets are high-entropy random strings, so a fast unsalted hash is
    enough: there is nothing to brute-force (domain-model section 8).
    """
    return TokenHash(hashlib.sha256(secret.encode()).hexdigest())
