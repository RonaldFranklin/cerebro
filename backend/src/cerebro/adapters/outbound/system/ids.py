"""Identifier and secret generation for the ``IdGenerator`` port."""

import secrets
import threading
import uuid

_UUID7_LOCK = threading.Lock()


class SystemIdGenerator:
    """Proposal ids are UUIDv7 (48-bit millisecond timestamp first, then a counter and random bits), rendered as
    32 lowercase hex characters: time-sortable as plain strings and inside the 1-64 character id alphabet.

    CPython 3.14's ``uuid.uuid7()`` keeps a process-wide counter, so ids created in the same millisecond still
    increase monotonically within one process; a module-level lock around the call keeps that ordering across
    threads (the counter state of ``uuid7`` is not documented as thread-safe). Across processes, order within the
    same millisecond is not guaranteed.

    The id embeds the creation time (to the millisecond) deliberately: it makes ids sortable, and the proposal
    already stores ``created_at``, so nothing new is disclosed.
    """

    def new_proposal_id(self) -> str:
        with _UUID7_LOCK:
            return uuid.uuid7().hex

    def new_token_id(self) -> str:
        # Not secret and not sortable: 64 random bits, with a prefix that makes it recognisable in logs.
        return f"tok-{secrets.token_hex(8)}"

    def new_token_secret(self) -> str:
        # 32 random bytes = 256 bits, 43 URL-safe characters.
        return secrets.token_urlsafe(32)
