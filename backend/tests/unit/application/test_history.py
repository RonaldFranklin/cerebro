from dataclasses import replace

import pytest

from cerebro.application.errors import PermissionDenied
from cerebro.application.history import ChangeUnavailable, HistoryRequest
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
