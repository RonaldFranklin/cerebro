"""Agent activity the owner can watch live (ADR-0031). Events carry no note content and are never written to disk."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from cerebro.domain.enums import Sensitivity
from cerebro.domain.note import NoteId

Verb = Literal["read", "write"]


@dataclass(frozen=True, slots=True, kw_only=True)
class ActivityEvent:
    """``sensitivity`` is the note's at that moment. It filters the event and is never sent to a client."""

    note_id: NoteId
    verb: Verb
    agent: str
    at: datetime
    sensitivity: Sensitivity


class ActivitySubscription(Protocol):
    def take(self) -> tuple[ActivityEvent, ...]:
        """The queued events, oldest first, emptying the queue."""
        ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordActivityRequest:
    note_id: NoteId
    verb: Verb
    sensitivity: Sensitivity


@dataclass(frozen=True, slots=True, kw_only=True)
class FollowActivityRequest:
    wake: Callable[[], None]
    """Called after an event is queued for this subscriber. It must return at once."""


@dataclass(frozen=True, slots=True, kw_only=True)
class VisibleActivityRequest:
    event: ActivityEvent
