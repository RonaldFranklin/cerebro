"""The owner's dry-run check of an edit: the write path's rules, nothing written."""

from dataclasses import replace

import pytest

from cerebro.application.dto import OwnerEditNoteRequest
from cerebro.application.errors import NoteNotFound, PermissionDenied
from cerebro.application.use_cases import CheckOwnerEdit
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Sensitivity
from cerebro.domain.note import Note, NoteId
from cerebro.domain.report import ViolationCode
from tests.fakes.system import FixedClock
from tests.unit.application.world import World, ctx, standard_world
from tests.unit.application.write_kit import SECRET
from tests.unit.domain.builders import VOCABULARY

SSH = NoteId("server-ssh-access")
OWNER = ctx(Sensitivity.RESTRICTED, owner=True)


def kit() -> tuple[World, CheckOwnerEdit, Note]:
    world = standard_world()
    stored = world.notes.get(SSH)
    assert stored
    # The shared fixture gives this note a reference above its own sensitivity; these checks start from a clean draft.
    note = replace(stored, related=())
    return world, CheckOwnerEdit(world.notes, FixedClock(), VOCABULARY), note


def stored_hash(world: World) -> str:
    stored = world.notes.get(SSH)
    assert stored
    return content_hash(stored)


def test_a_clean_draft_passes_and_nothing_is_written() -> None:
    world, check, note = kit()
    before = world.notes.head()
    result = check.execute(OwnerEditNoteRequest(note=note, expected_hash=stored_hash(world)), OWNER)
    assert result.validation.ok
    assert result.base_current
    assert result.link_lines == ()
    assert (result.soft_limit, result.hard_limit) == (1500, 3000)
    assert result.words == len(note.body.split())
    assert world.notes.head() == before


def test_every_problem_is_reported_with_its_line_and_never_its_value() -> None:
    _, check, note = kit()
    body = f"Fine.\nSee [[not-yet-written]].\n{SECRET}\nAlso [[not-yet-written]] again.\n"
    result = check.execute(
        OwnerEditNoteRequest(note=replace(note, body=body, sensitivity=Sensitivity.PUBLIC), expected_hash="0" * 64),
        OWNER,
    )
    codes = {violation.code for violation in result.validation.violations}
    assert {ViolationCode.BROKEN_LINK, ViolationCode.SECRET_DETECTED} <= codes
    assert result.link_lines == (2, 4)
    assert not result.base_current
    assert all(SECRET not in violation.message for violation in result.validation.violations)


def test_a_link_to_a_more_sensitive_note_names_its_line() -> None:
    world, check, note = kit()
    hidden = next(n for n in world.notes.scan().notes if n.sensitivity > note.sensitivity)
    changed = replace(note, body=f"One.\nTwo [[{hidden.id.value}]].")
    result = check.execute(OwnerEditNoteRequest(note=changed, expected_hash=content_hash(note)), OWNER)
    assert not result.validation.ok
    assert result.link_lines == (2,)


@pytest.mark.parametrize(("words", "ok"), [(1501, True), (3001, False)])
def test_the_budget_of_the_note_type_is_reported(words: int, ok: bool) -> None:
    _, check, note = kit()
    changed = replace(note, body="word " * words)
    result = check.execute(OwnerEditNoteRequest(note=changed, expected_hash=content_hash(note)), OWNER)
    assert (result.words, result.validation.ok) == (words, ok)


def test_hubs_have_their_own_budget() -> None:
    world, check, _ = kit()
    hub = next(n for n in world.notes.scan().notes if n.is_hub)
    result = check.execute(OwnerEditNoteRequest(note=hub, expected_hash=content_hash(hub)), OWNER)
    assert (result.soft_limit, result.hard_limit) == (None, 800)


@pytest.mark.parametrize(
    "context",
    [ctx(Sensitivity.RESTRICTED), ctx(Sensitivity.INTERNAL, owner=True), ctx(Sensitivity.PUBLIC, owner=True)],
)
def test_only_a_restricted_owner_may_check(context: object) -> None:
    _, check, note = kit()
    with pytest.raises(PermissionDenied):
        check.execute(OwnerEditNoteRequest(note=note, expected_hash=content_hash(note)), context)  # type: ignore[arg-type]


def test_a_missing_note_cannot_be_checked() -> None:
    _, check, note = kit()
    absent = replace(note, id=NoteId("absent-note"))
    with pytest.raises(NoteNotFound):
        check.execute(OwnerEditNoteRequest(note=absent, expected_hash="0" * 64), OWNER)
