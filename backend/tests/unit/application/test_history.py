from dataclasses import replace

import pytest

from cerebro.application.errors import PermissionDenied
from cerebro.application.history import MAX_DIFF_CHARS, ChangeDetail, ChangeUnavailable, HistoryRequest
from cerebro.application.use_cases.history import OwnerHistory
from cerebro.domain.enums import Sensitivity
from cerebro.domain.note import NoteId
from tests.fakes.history import FakeHistory
from tests.unit.application.world import ctx, standard_world
from tests.unit.domain.builders import make_note


def test_history_authorizes_current_and_both_versions_and_redacts_links() -> None:
    world = standard_world()
    base = make_note(id=NoteId("server-ssh-access"), sensitivity=Sensitivity.INTERNAL, body="Before [[gone-note]].")
    after = replace(base, body="After [[gone-note|alias]].")
    port = FakeHistory(base, after)
    api = OwnerHistory(world.notes, port)
    owner = ctx(owner=True)
    with pytest.raises(PermissionDenied):
        api.detail(HistoryRequest(port.revision), ctx())
    result = api.detail(HistoryRequest(port.revision), owner)
    assert result.change.actor == "codex"
    assert "Before" in result.diff
    assert "After" in result.diff
    assert "gone-note" not in result.diff
    port.before = replace(base, sensitivity=Sensitivity.RESTRICTED)
    assert not api.list(owner)
    with pytest.raises(ChangeUnavailable):
        api.detail(HistoryRequest(port.revision), owner)


@pytest.mark.parametrize("hidden", ["current", "before", "after"])
def test_visibility_of_each_version_is_required_for_detail_and_listing(hidden: str) -> None:
    world = standard_world()
    current = world.notes.get(NoteId("vault-layout" if hidden == "current" else "server-ssh-access"))
    assert current
    before = replace(current, sensitivity=Sensitivity.RESTRICTED if hidden == "before" else Sensitivity.INTERNAL)
    after = replace(current, sensitivity=Sensitivity.RESTRICTED if hidden == "after" else Sensitivity.INTERNAL)
    port = FakeHistory(before, after)
    history = OwnerHistory(world.notes, port)
    owner = ctx(owner=True)
    assert history.list(owner) == ()
    with pytest.raises(ChangeUnavailable):
        history.detail(HistoryRequest(port.revision), owner)


@pytest.mark.parametrize("broken", ["revision", "parent"])
def test_an_unreadable_version_is_left_out_of_the_list_and_unavailable_in_detail(broken: str) -> None:
    world = standard_world()
    note = world.notes.get(NoteId("server-ssh-access"))
    assert note
    port = FakeHistory(note, replace(note, body="After."))
    port.unreadable.add(getattr(port, broken))
    history = OwnerHistory(world.notes, port)
    assert history.list(ctx(owner=True)) == ()
    with pytest.raises(ChangeUnavailable):
        history.detail(HistoryRequest(port.revision), ctx(owner=True))


def _detail(before_body: str, after_body: str) -> ChangeDetail:
    world = standard_world()
    base = make_note(id=NoteId("server-ssh-access"), sensitivity=Sensitivity.INTERNAL, body=before_body)
    port = FakeHistory(base, replace(base, body=after_body))
    return OwnerHistory(world.notes, port).detail(HistoryRequest(port.revision), ctx(owner=True))


LONG_BODY = "".join(f"Line {number} of a long synthetic note.\n" for number in range(6000))


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (LONG_BODY, LONG_BODY + "Appended conclusion.\n"),
        (("long paragraph " * 70 + "\n") * 20, ("long paragraph " * 70 + "\n") * 20 + "Appended conclusion.\n"),
    ],
    ids=["many-lines", "many-characters"],
)
def test_appending_to_a_long_note_shows_the_addition(before: str, after: str) -> None:
    result = _detail(before, after)
    assert "+Appended conclusion." in result.diff.splitlines()
    assert len(result.diff) <= MAX_DIFF_CHARS


def test_hunk_positions_refer_to_the_whole_document_and_distant_edits_are_both_shown() -> None:
    after = LONG_BODY.replace("Line 3000 of", "Middle edit of").replace("Line 5999 of", "Final edit of")
    result = _detail(LONG_BODY, after)
    lines = result.diff.splitlines()
    assert "+Middle edit of a long synthetic note." in lines
    assert "+Final edit of a long synthetic note." in lines
    header = next(line for line in lines if line.startswith("@@"))
    position = int(header.split()[1].removeprefix("-").split(",")[0])
    # The hunk starts three context lines before body line 3000, after the frontmatter.
    assert lines[lines.index(header) + 4] == "-Line 3000 of a long synthetic note."
    assert 3000 - 2 < position < 3000 + 30
    assert not result.truncated


def test_only_a_clipped_output_is_marked_truncated() -> None:
    result = _detail("Short.\n", LONG_BODY)
    assert result.truncated
    assert len(result.diff) == MAX_DIFF_CHARS


def test_a_rewrite_too_large_to_align_is_shown_as_one_replacement() -> None:
    # Both sides of the changed region exceed the comparison budget; the shared line is not re-aligned.
    before = "old\n" * 4100 + "shared\n" + "old\n"
    after = "new\n" + "shared\n" + "new\n" * 4100
    result = _detail(before, after)
    body = result.diff.split("@@\n", 1)[1].splitlines()
    assert body[0] == "-old"
    assert "+new" in body
    assert " shared" not in body
    assert result.truncated
    assert len(result.diff) <= MAX_DIFF_CHARS


def test_a_clipped_removed_side_is_marked_truncated_even_when_the_additions_are_short() -> None:
    result = _detail("a long removed line\n" * 4100, "n\n" * 4001)
    assert "+n" in result.diff.splitlines()
    assert result.truncated


@pytest.mark.parametrize(("lines", "width"), [(150, 70), (1, 10_000)], ids=["many-lines", "one-long-line"])
def test_a_clipped_rewrite_shows_both_removals_and_additions(lines: int, width: int) -> None:
    result = _detail(("o" * width + "\n") * lines, ("n" * width + "\n") * lines)
    shown = result.diff.splitlines()
    assert any(line.startswith("-ooo") for line in shown)
    assert any(line.startswith("+nnn") for line in shown)
    assert result.truncated
    assert len(result.diff) <= MAX_DIFF_CHARS


@pytest.mark.parametrize(
    ("before", "after"),
    [("Text.", "Text.\n"), ("Text.\n", "Text."), ("One.\r\nTwo.", "One.\nTwo.")],
    ids=["newline-added", "newline-removed", "line-ending"],
)
def test_line_ending_changes_are_not_an_empty_diff(before: str, after: str) -> None:
    result = _detail(before, after)
    assert result.diff
    assert not result.truncated


def test_links_far_into_a_long_note_are_redacted() -> None:
    result = _detail(LONG_BODY, LONG_BODY + "See [[gone-note|secret alias]].\n")
    assert "+See [[redacted link]]." in result.diff.splitlines()
    assert "secret alias" not in result.diff
