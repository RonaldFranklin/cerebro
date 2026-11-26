"""Time, ids, secrets, encryption and backup: small outbound ports."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, timezone-aware (UTC)."""
        ...


class IdGenerator(Protocol):
    def new_note_id(self) -> str:
        """An unpredictable, unique kebab-case identifier for an agent-created note."""
        ...

    def new_edit_id(self) -> str:
        """A unique, time-sortable owner edit id (1-64 characters of letters, digits, ``_`` or ``-``)."""
        ...

    def new_token_id(self) -> str:
        """A unique token id (same alphabet as owner edit ids)."""
        ...

    def new_token_secret(self) -> str:
        """A new unguessable token secret: at least 256 bits from a CSPRNG (the real adapter uses
        ``secrets.token_urlsafe(32)``), never time- or counter-derived. Shown once at issue time; only its SHA-256
        hash is stored. ``IssueAgentToken`` rejects anything shorter than 32 characters but cannot measure entropy."""
        ...


class Cipher(Protocol):
    """Encrypts the payload of restricted notes. Used only by the filesystem adapter; the core sees plaintext."""

    def encrypt(self, plaintext: bytes) -> bytes: ...

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Raises ``DecryptionError`` for a wrong key or tampered data."""
        ...


class VersionControl(Protocol):
    """Backup side of the content repository. Commits are made by ``NoteRepository.apply`` (one commit per
    applied change, with the trailers of ``CommitMessage``); this port only publishes them.
    """

    def push(self) -> None:
        """Push the commits to the backup remote. A no-op when none is configured."""
        ...
