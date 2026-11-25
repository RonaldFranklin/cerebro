"""Typed application exceptions: expected outcomes of use cases and ports.

Inbound adapters map each of these to their own error format in one place. Domain errors
(``InvalidValueError``, ``InvalidTransition``, ``ContentConflict``) propagate unchanged.
"""

from cerebro.domain.note import NoteId


class ApplicationError(Exception):
    """Base class for every error raised by the application layer."""


class NoteNotFound(ApplicationError):  # noqa: N818
    """The note does not exist **or is not visible to the caller**; the two cases are indistinguishable.

    Never add a reason, a sensitivity or a hint to this error: it must not leak that a hidden note exists.
    """

    def __init__(self, note_id: NoteId) -> None:
        super().__init__(f"note not found: {note_id.value}")
        self.note_id = note_id


class ProposalNotFound(ApplicationError):  # noqa: N818
    def __init__(self, proposal_id: str) -> None:
        super().__init__(f"proposal not found: {proposal_id}")
        self.proposal_id = proposal_id


class TokenNotFound(ApplicationError):  # noqa: N818
    def __init__(self, token_id: str) -> None:
        super().__init__(f"token not found: {token_id}")
        self.token_id = token_id


class AlreadyExists(ApplicationError):  # noqa: N818
    """A record with the same unique key (note id, proposal id, token id or token hash) is already stored."""

    def __init__(self, entity: str, key: str) -> None:
        super().__init__(f"{entity} already exists: {key}")
        self.entity = entity
        self.key = key


class PermissionDenied(ApplicationError):  # noqa: N818
    """The caller's principal or scopes do not allow the action (checked in the use cases)."""

    def __init__(self, action: str) -> None:
        super().__init__(f"not allowed: {action}")
        self.action = action


class DecryptionError(ApplicationError):
    """A payload could not be decrypted (wrong key or tampered data)."""
