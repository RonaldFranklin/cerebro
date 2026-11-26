"""Private change-history DTOs."""

import re
from dataclasses import dataclass
from typing import Literal

from cerebro.application.errors import ApplicationError
from cerebro.domain.note import NoteId

RECENT_WINDOW = 32
MAX_CHANGES = 20
MAX_DIFF_CHARS = 16_384
_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def is_revision(value: str) -> bool:
    return _REVISION.fullmatch(value) is not None


class ChangeUnavailable(ApplicationError):  # noqa: N818
    """Missing, unavailable and forbidden revisions share one response."""


@dataclass(frozen=True, slots=True)
class HistoryCursor:
    head: str
    start: str


@dataclass(frozen=True, slots=True)
class HistoryRequest:
    revision: str
    cursor: HistoryCursor | None = None


@dataclass(frozen=True, slots=True)
class HistoryVersion:
    revision: str
    parent: str | None
    note_id: NoteId
    actor: str
    timestamp: str
    operation: Literal["create", "update"]


@dataclass(frozen=True, slots=True)
class ChangeSummary:
    revision: str
    note_id: str
    title: str
    actor: str
    timestamp: str
    operation: str


@dataclass(frozen=True, slots=True)
class ChangePage:
    page_cursor: HistoryCursor | None
    changes: tuple[ChangeSummary, ...]
    next_cursor: HistoryCursor | None


@dataclass(frozen=True, slots=True)
class ChangeDetail:
    change: ChangeSummary
    diff: str
    truncated: bool
