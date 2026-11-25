"""Test data builders for domain objects."""

from datetime import date
from typing import Any

from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.note import Note, NoteId
from cerebro.domain.vocabulary import INITIAL_USER_NOTE_TYPES, Area, NoteType, Vocabulary

VALID_SUMMARY = "How to reach the home server over SSH from the workstation, and how to verify the host."


def make_note(**overrides: Any) -> Note:
    fields: dict[str, Any] = {
        "id": NoteId("server-ssh-access"),
        "title": "SSH access to the home server",
        "type": NoteType("runbook"),
        "area": Area("infrastructure"),
        "priority": Priority.P2,
        "sensitivity": Sensitivity.RESTRICTED,
        "status": Status.VERIFIED,
        "summary": VALID_SUMMARY,
        "hub": NoteId("infrastructure"),
        "related": (NoteId("server-docker-conventions"),),
        "superseded_by": None,
        "lang": "en",
        "created": date(2026, 11, 24),
        "updated": date(2026, 11, 24),
        "review_by": None,
        "body": "# SSH access\n\nUse the workstation key.\n",
    }
    fields.update(overrides)
    return Note(**fields)


def make_raw(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "id": "server-ssh-access",
        "title": "SSH access to the home server",
        "type": "runbook",
        "area": "infrastructure",
        "priority": "P2",
        "sensitivity": "restricted",
        "status": "verified",
        "summary": VALID_SUMMARY,
        "hub": "infrastructure",
        "related": ["server-docker-conventions"],
        "superseded_by": None,
        "lang": "en",
        "created": date(2026, 11, 24),
        "updated": date(2026, 11, 24),
        "review_by": date(2027, 5, 24),
    }
    raw.update(overrides)
    return raw


VOCABULARY = Vocabulary(areas=frozenset({Area("infrastructure")}), types=INITIAL_USER_NOTE_TYPES)


def words(count: int) -> str:
    return " ".join(["word"] * count)
