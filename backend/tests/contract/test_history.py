from dataclasses import replace
from pathlib import Path

import pytest

from cerebro.adapters.outbound.filesystem.history import GitChangeHistory
from cerebro.application.history import ChangeUnavailable
from cerebro.application.ports.history import ChangeHistory
from cerebro.application.ports.notes import NoteChange
from cerebro.bootstrap.container import build_container
from cerebro.domain.commit_message import AgentWriteCommit
from cerebro.domain.content_hash import content_hash
from cerebro.domain.note import NoteId
from tests.fakes.history import FakeHistory
from tests.integration.content_kit import VAULT_ID, make_sample_repo, sample_notes


@pytest.mark.parametrize("real", [False, True], ids=["fake", "git"])
def test_attributed_update_reads_the_exact_versions(real: bool, tmp_path: Path) -> None:
    before = next(n for n in sample_notes() if n.id == VAULT_ID)
    after = replace(before, body="A corrected restricted procedure.")
    store: ChangeHistory
    if real:
        sample = make_sample_repo(tmp_path)
        container = build_container(sample.settings)
        container.notes.apply(
            [NoteChange(note=after, expected_hash=content_hash(before))],
            AgentWriteCommit(note_id=after.id, written_by="codex"),
        )
        store = GitChangeHistory(sample.git(), container.cipher)
    else:
        store = FakeHistory(before, after)
    revision = store.recent()[0]
    change = store.change(revision)
    assert change is not None
    assert change.parent is not None
    assert change.actor == "codex"
    assert change.operation == "update"
    assert store.note_at(revision, after.id) == after
    assert store.note_at(change.parent, after.id) == before
    assert store.note_at(revision, NoteId("absent")) is None


@pytest.mark.parametrize("real", [False, True], ids=["fake", "git"])
def test_unknown_revisions_and_unreadable_blobs_fail_closed(real: bool, tmp_path: Path) -> None:
    note = next(n for n in sample_notes() if n.id == VAULT_ID)
    unknown = "0" * 39 + "1"
    store: ChangeHistory
    if real:
        sample = make_sample_repo(tmp_path)
        path = f"notes/{VAULT_ID.value}.md"
        (sample.root / path).write_text("not a note")
        git = sample.git()
        git.stage([path])
        broken = git.commit("Break a note", [path])
        store = GitChangeHistory(git, build_container(sample.settings).cipher)
    else:
        fake = FakeHistory(note, note)
        broken = fake.revision
        fake.unreadable.add(broken)
        store = fake
    with pytest.raises(ChangeUnavailable):
        store.note_at(broken, VAULT_ID)
    with pytest.raises(ChangeUnavailable):
        store.note_at(unknown, VAULT_ID)
    with pytest.raises(ChangeUnavailable):
        store.change(unknown)
