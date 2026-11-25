"""Identifier and secret generation for the ``IdGenerator`` port."""

import secrets
import uuid


class SystemIdGenerator:
    """Proposal ids are UUIDv7 (48-bit millisecond timestamp first, then a counter and random bits), rendered as
    32 lowercase hex characters: time-sortable as plain strings and inside the 1-64 character id alphabet.

    CPython 3.14's ``uuid.uuid7()`` keeps a process-wide counter, so ids created in the same millisecond still
    increase monotonically within one process. Across processes, order within the same millisecond is not guaranteed.
    """

    def new_proposal_id(self) -> str:
        return uuid.uuid7().hex

    def new_token_id(self) -> str:
        # Not secret and not sortable: 64 random bits, with a prefix that makes it recognisable in logs.
        return f"tok-{secrets.token_hex(8)}"

    def new_token_secret(self) -> str:
        # 32 random bytes = 256 bits, 43 URL-safe characters.
        return secrets.token_urlsafe(32)
