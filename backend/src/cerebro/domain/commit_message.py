"""Commit messages of applied changes: one ``Written-by`` trailer naming the authenticated writer (ADR-0023).

Every value is validated, never silently altered: anything that could add a line, a fake trailer or control
characters raises ``InvalidValueError``. Subjects are built from the note id (a slug), never from free text.
"""

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Final

from cerebro.domain.access import validate_agent_name
from cerebro.domain.content_hash import is_content_hash
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.note import NoteId

MAX_SUBJECT_LENGTH = 100
OWNER: Final = "owner"
"""The writer recorded for owner edits. ``validate_agent_name`` reserves it, so no agent can appear under it."""


@dataclass(frozen=True, slots=True, kw_only=True)
class OwnerCommit:
    """A commit written by the owner: ``subject``, a blank line and ``Written-by: owner``."""

    subject: str

    def __post_init__(self) -> None:
        if not isinstance(self.subject, str) or not self.subject.strip():
            raise InvalidValueError("subject", "must be a non-blank string")
        if len(self.subject) > MAX_SUBJECT_LENGTH:
            raise InvalidValueError("subject", f"longer than {MAX_SUBJECT_LENGTH} characters")
        if any(unicodedata.category(c)[0] in "CZ" and c != " " for c in self.subject):
            raise InvalidValueError("subject", "must be a single line without control characters")
        if self.subject != self.subject.strip():
            raise InvalidValueError("subject", "must not start or end with whitespace")

    @classmethod
    def for_update(cls, note_id: NoteId) -> OwnerCommit:
        """The commit of an owner edit of an existing note."""
        if not isinstance(note_id, NoteId):
            raise InvalidValueError("note_id", "expected a note id")
        return cls(subject=f"Update note: {note_id.value}")

    def render(self) -> str:
        return f"{self.subject}\n\nWritten-by: {OWNER}"


def create_request_key(token_id: str, request_id: str) -> str:
    """A stable opaque identifier for this authenticated token's client request."""
    encoded = json.dumps([token_id, request_id], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentWriteCommit:
    note_id: NoteId
    written_by: str
    request_key: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.note_id, NoteId):
            raise InvalidValueError("note_id", "expected a note id")
        validate_agent_name(self.written_by)
        if self.request_key is not None and not is_content_hash(self.request_key):
            raise InvalidValueError("request_key", "a create request key must be a SHA-256 digest")

    @property
    def subject(self) -> str:
        verb = "Add" if self.request_key is not None else "Update"
        return f"{verb} note: {self.note_id.value}"

    def render(self) -> str:
        message = f"{self.subject}\n\nWritten-by: {self.written_by}"
        if self.request_key is not None:
            message += f"\nWrite-Request: {self.request_key}"
        return message
