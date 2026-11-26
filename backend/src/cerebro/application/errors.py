"""Typed application exceptions: expected outcomes of use cases and ports.

Inbound adapters map each of these to their own error format in one place. Domain errors
(``InvalidValueError``, ``ContentConflict``) propagate unchanged.
"""

from cerebro.domain.note import NoteId


class ApplicationError(Exception):
    """Base class for every error raised by the application layer."""


class NoteReadError(ApplicationError):
    """Stored note data is unreadable or ambiguous."""


class NoteNotFound(ApplicationError):  # noqa: N818
    """The note does not exist **or is not visible to the caller**; the two cases are indistinguishable.

    Never add a reason, a sensitivity or a hint to this error: it must not leak that a hidden note exists.
    """

    def __init__(self, note_id: NoteId) -> None:
        super().__init__("note not found")
        self.note_id = note_id


class TokenNotFound(ApplicationError):  # noqa: N818
    def __init__(self, token_id: str) -> None:
        super().__init__("token not found")
        self.token_id = token_id


class AuthenticationFailed(ApplicationError):  # noqa: N818
    """The presented token secret does not authenticate: unknown, malformed, revoked or expired.

    One fixed message for every cause, so a caller cannot tell a revoked token from an unknown one.
    """

    def __init__(self) -> None:
        super().__init__("authentication failed")


class AlreadyExists(ApplicationError):  # noqa: N818
    """A record with the same unique key (note id, token id or token hash) is already stored."""

    def __init__(self, entity: str, key: str) -> None:
        super().__init__(f"{entity} already exists")
        self.entity = entity
        self.key = key


class PermissionDenied(ApplicationError):  # noqa: N818
    """The caller's principal or scopes do not allow the action (checked in the use cases)."""

    def __init__(self, action: str) -> None:
        super().__init__(f"not allowed: {action}")
        self.action = action


class DecryptionError(ApplicationError):
    """A payload could not be decrypted (wrong key or tampered data)."""


class SectionNotFound(ApplicationError):  # noqa: N818
    """A **visible** note has no heading with this title. Only raised after the visibility check, so it never
    reveals anything about hidden notes.
    """

    def __init__(self, note_id: NoteId, section: str) -> None:
        super().__init__("section not found")
        self.note_id = note_id
        self.section = section


class PasswordDurabilityError(ApplicationError):
    """The new owner verifier is active, but its directory entry could not be synced."""

    def __init__(self) -> None:
        super().__init__("owner password updated, durability unconfirmed")
