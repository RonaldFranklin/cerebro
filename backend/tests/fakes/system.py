"""Deterministic fakes of the small system ports."""

import base64
import hashlib
from datetime import UTC, datetime, timedelta

from cerebro.application.errors import DecryptionError

START = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


class FixedClock:
    """Returns a fixed time until moved with ``advance`` / ``set``."""

    def __init__(self, now: datetime = START) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, now: datetime) -> None:
        self._now = now


class SequentialIdGenerator:
    """``edit-0001``, ``token-0001``, ... and secrets derived from a counter (deterministic, not secure)."""

    def __init__(self) -> None:
        self._counters = {"note": 0, "edit": 0, "token": 0, "secret": 0}

    def _next(self, kind: str) -> int:
        self._counters[kind] += 1
        return self._counters[kind]

    def new_note_id(self) -> str:
        return f"note-{self._next('note'):04d}"

    def new_edit_id(self) -> str:
        return f"edit-{self._next('edit'):04d}"

    def new_token_id(self) -> str:
        return f"token-{self._next('token'):04d}"

    def new_token_secret(self) -> str:
        n = self._next("secret")
        return hashlib.sha256(f"secret-{n}".encode()).hexdigest()


class ReversingCipher:
    """Not real encryption: a reversible transform with an integrity check, enough to test the contract."""

    _PREFIX = b"fake-cipher:"

    def encrypt(self, plaintext: bytes) -> bytes:
        digest = hashlib.sha256(plaintext).digest()[:8]
        return self._PREFIX + base64.b64encode(digest + plaintext[::-1])

    def decrypt(self, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(self._PREFIX):
            raise DecryptionError("not a payload of this cipher")
        try:
            raw = base64.b64decode(ciphertext[len(self._PREFIX) :], validate=True)
        except ValueError:
            raise DecryptionError("corrupted payload") from None
        digest, plaintext = raw[:8], raw[8:][::-1]
        if hashlib.sha256(plaintext).digest()[:8] != digest:
            raise DecryptionError("corrupted payload")
        return plaintext


class RecordingVersionControl:
    """Counts pushes."""

    def __init__(self) -> None:
        self.pushes = 0

    def push(self) -> None:
        self.pushes += 1


__all__ = ["START", "FixedClock", "RecordingVersionControl", "ReversingCipher", "SequentialIdGenerator"]
