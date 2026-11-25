"""The curator's ``Decision`` on a proposal."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from cerebro.domain.errors import InvalidValueError
from cerebro.domain.note import NoteId

MAX_REASON_LENGTH = 1000


class DecisionKind(StrEnum):
    REJECT = "reject"
    MERGE_INTO = "merge_into"
    CREATE = "create"
    NEEDS_OWNER = "needs_owner"


@dataclass(frozen=True, slots=True, kw_only=True)
class Decision:
    """What the curator decided. Required fields per variant:

    - ``reject``: ``reason``; no target.
    - ``merge_into``: ``target`` (the existing note to merge into); reason optional.
    - ``create``: ``target`` (the id of the note to create); reason optional.
    - ``needs_owner``: ``reason``; ``target`` optional (a suggested note id).
    """

    kind: DecisionKind
    target: NoteId | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DecisionKind):
            raise InvalidValueError("kind", "expected a DecisionKind")
        if self.target is not None and not isinstance(self.target, NoteId):
            raise InvalidValueError("target", "expected a NoteId")
        if self.reason is not None:
            if not isinstance(self.reason, str):
                raise InvalidValueError("reason", "expected a string")
            if len(self.reason) > MAX_REASON_LENGTH:
                raise InvalidValueError("reason", f"longer than {MAX_REASON_LENGTH} characters")
        needs_reason = self.kind in (DecisionKind.REJECT, DecisionKind.NEEDS_OWNER)
        if needs_reason and (self.reason is None or not self.reason.strip()):
            raise InvalidValueError("reason", f"required for {self.kind.value}")
        if self.kind in (DecisionKind.MERGE_INTO, DecisionKind.CREATE) and self.target is None:
            raise InvalidValueError("target", f"required for {self.kind.value}")
        if self.kind is DecisionKind.REJECT and self.target is not None:
            raise InvalidValueError("target", "not allowed for reject")

    @classmethod
    def reject(cls, reason: str) -> Self:
        return cls(kind=DecisionKind.REJECT, reason=reason)

    @classmethod
    def merge_into(cls, target: NoteId, reason: str | None = None) -> Self:
        return cls(kind=DecisionKind.MERGE_INTO, target=target, reason=reason)

    @classmethod
    def create(cls, target: NoteId, reason: str | None = None) -> Self:
        return cls(kind=DecisionKind.CREATE, target=target, reason=reason)

    @classmethod
    def needs_owner(cls, reason: str, target: NoteId | None = None) -> Self:
        return cls(kind=DecisionKind.NEEDS_OWNER, target=target, reason=reason)
