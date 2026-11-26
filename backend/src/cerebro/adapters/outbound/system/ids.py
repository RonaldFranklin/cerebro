"""Identifier and secret generation for the ``IdGenerator`` port."""

import secrets
import string


class SystemIdGenerator:
    def new_note_suffix(self) -> str:
        return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(4))

    def new_token_id(self) -> str:
        # Not secret and not sortable: 64 random bits, with a prefix that makes it recognisable in logs.
        return f"tok-{secrets.token_hex(8)}"

    def new_token_secret(self) -> str:
        # 32 random bytes = 256 bits, 43 URL-safe characters.
        return secrets.token_urlsafe(32)
