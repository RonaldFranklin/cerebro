import dataclasses
from datetime import date

import pytest

from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.note import Note, NoteId
from cerebro.domain.vocabulary import Area, NoteType


def make_note(**overrides: object) -> Note:
    fields: dict[str, object] = {
        "id": NoteId("server-ssh-access"),
        "title": "SSH access to the home server",
        "type": NoteType("runbook"),
        "area": Area("infrastructure"),
        "priority": Priority.P2,
        "sensitivity": Sensitivity.RESTRICTED,
        "status": Status.VERIFIED,
        "summary": "How to reach the home server over SSH from the workstation.",
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
    return Note(**fields)  # type: ignore[arg-type]


def test_holds_typed_frontmatter_and_body() -> None:
    note = make_note()

    assert note.id == NoteId("server-ssh-access")
    assert note.priority is Priority.P2
    assert note.related == (NoteId("server-docker-conventions"),)
    assert note.body.startswith("# SSH access")


def test_is_immutable_and_keyword_only() -> None:
    note = make_note()
    with pytest.raises(dataclasses.FrozenInstanceError):
        note.title = "Other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        Note(NoteId("a"))  # type: ignore[call-arg]


def test_changes_produce_a_new_instance() -> None:
    note = make_note()
    changed = dataclasses.replace(note, status=Status.SUPERSEDED, superseded_by=NoteId("server-ssh-v2"))

    assert note.status is Status.VERIFIED
    assert changed.status is Status.SUPERSEDED


def test_related_must_be_a_tuple() -> None:
    with pytest.raises(TypeError):
        make_note(related=[NoteId("a")])


@pytest.mark.parametrize(
    ("note_type", "expected"),
    [("hub", True), ("core", False), ("runbook", False)],
)
def test_is_hub(note_type: str, expected: bool) -> None:
    assert make_note(type=NoteType(note_type)).is_hub is expected


def test_is_current_follows_status() -> None:
    assert make_note(status=Status.APPLIED).is_current
    assert not make_note(status=Status.ARCHIVED).is_current
