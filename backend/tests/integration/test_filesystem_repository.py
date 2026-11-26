"""Integration tests of ``FilesystemNoteRepository`` against real files and the real ``git`` CLI."""

import os
import shutil
import stat
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from cerebro.adapters.outbound.crypto import FernetCipher, generate_key
from cerebro.adapters.outbound.filesystem import (
    FilesystemNoteRepository,
    NoteFileError,
    NoteWriteError,
    VocabularyError,
    WriterBusyError,
    load_vocabulary,
    safeio,
)
from cerebro.adapters.outbound.filesystem import repository as repository_module
from cerebro.adapters.outbound.filesystem.codec import parse_note_bytes
from cerebro.adapters.outbound.git import GitError, GitRepo
from cerebro.application.ports import Cipher, NoteChange, NoteRepository
from cerebro.application.ports.notes import CommittedCreate
from cerebro.domain.commit_message import AgentWriteCommit, OwnerCommit, create_request_key
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.errors import ContentConflict, InvalidValueError
from cerebro.domain.note import Note, NoteId
from cerebro.domain.vocabulary import CORE, HUB, Area, NoteType
from tests.contract.notes_contract import MESSAGE, NoteRepositoryContract
from tests.integration.test_git import make_repo, raw_git
from tests.unit.domain.builders import make_note


def messages(git: GitRepo, count: int = 10) -> list[str]:
    """The newest ``count`` first-parent commit messages."""
    return [git.describe_commit(revision)[1] for revision in git.recent_revisions(count)]


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the git executable is not installed")

Make = Callable[..., FilesystemNoteRepository]


@pytest.fixture
def cipher() -> Cipher:
    return FernetCipher([generate_key()])


@pytest.fixture
def git(tmp_path: Path) -> GitRepo:
    return make_repo(tmp_path)


@pytest.fixture
def root(tmp_path: Path, git: GitRepo) -> Path:
    return tmp_path / "repo"


@pytest.fixture
def repo(git: GitRepo, root: Path, cipher: Cipher) -> FilesystemNoteRepository:
    return FilesystemNoteRepository(root, git=git, cipher=cipher)


class TestFilesystemNoteRepositoryContract(NoteRepositoryContract):
    @pytest.fixture
    def repo(self, git: GitRepo, root: Path, cipher: Cipher) -> NoteRepository:
        return FilesystemNoteRepository(root, git=git, cipher=cipher)


def plain(**overrides: Any) -> Note:
    return make_note(sensitivity=Sensitivity.INTERNAL, **overrides)


def hub_note(**overrides: Any) -> Note:
    fields: dict[str, Any] = {
        "id": NoteId("infrastructure"),
        "type": HUB,
        "priority": Priority.P1,
        "sensitivity": Sensitivity.INTERNAL,
        "hub": None,
        "related": (),
    }
    return make_note(**{**fields, **overrides})


def core_note(**overrides: Any) -> Note:
    fields: dict[str, Any] = {
        "id": NoteId("owner-profile"),
        "type": CORE,
        "priority": Priority.P0,
        "sensitivity": Sensitivity.INTERNAL,
        "hub": None,
        "related": (),
    }
    return make_note(**{**fields, **overrides})


def tree_files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


# -- layout and format ---------------------------------------------------------------------------------------------


def test_reopened_repository_reads_the_original_created_note(
    git: GitRepo, root: Path, cipher: Cipher, repo: FilesystemNoteRepository
) -> None:
    note = make_note(sensitivity=Sensitivity.RESTRICTED)
    key = create_request_key("token-fixture", "request-fixture")
    message = AgentWriteCommit(note_id=note.id, written_by="test-agent", request_key=key)
    revision = repo.apply([NoteChange(note=note)], message)
    later = NoteChange(note=replace(note, body="Changed later."), expected_hash=content_hash(note))
    repo.apply([later], OwnerCommit.for_update(note.id))
    reopened = FilesystemNoteRepository(root, git=git, cipher=cipher)
    assert reopened.find_create(key) == CommittedCreate(revision=revision, note=note)
    text = git.describe_commit(revision)[1]
    assert text == f"Add note: {note.id.value}\n\nWritten-by: test-agent\nWrite-Request: {key}"
    wrong_key = FilesystemNoteRepository(root, git=git, cipher=FernetCipher([generate_key()]))
    with pytest.raises(NoteWriteError, match="invalid create record"):
        wrong_key.find_create(key)


@pytest.mark.parametrize("alter", ["note", "trailer", "subject"])
def test_malformed_create_record_fails_closed(repo: FilesystemNoteRepository, git: GitRepo, alter: str) -> None:
    note = plain()
    key = create_request_key("test-token", "first-request")
    message = AgentWriteCommit(note_id=note.id, written_by="test-agent", request_key=key)
    repo.apply([NoteChange(note=note)], message)
    text = message.render()
    if alter == "note":
        text = text.replace(f"Add note: {note.id.value}", "Add note: other-note")
    elif alter == "trailer":
        text += "\nWrite-Receipt: AAAA"
    else:
        text = text.replace("Add note:", "Update note:")
    # A newer matching record must never fall back to another commit or allow a fresh create.
    other = plain(id=NoteId("second-note"))
    repo.apply([NoteChange(note=other)], MESSAGE)
    git._run(["commit", "--amend", "--quiet", "-F", "-"], stdin=text)
    with pytest.raises(NoteWriteError, match="invalid create record"):
        repo.find_create(key)


def test_files_are_placed_by_type_and_named_after_the_id(repo: FilesystemNoteRepository, root: Path) -> None:
    repo.apply([NoteChange(note=core_note()), NoteChange(note=hub_note()), NoteChange(note=plain())], MESSAGE)
    assert sorted(tree_files(root)) == ["core/owner-profile.md", "hubs/infrastructure.md", "notes/server-ssh-access.md"]


def test_a_plain_note_file_has_the_canonical_form(repo: FilesystemNoteRepository, root: Path) -> None:
    repo.apply([NoteChange(note=plain(body="Body text.\n", review_by=None))], MESSAGE)
    text = (root / "notes/server-ssh-access.md").read_text()
    assert text.startswith("---\nid: server-ssh-access\ntitle: SSH access to the home server\ntype: runbook\n")
    assert text.endswith("updated: 2026-11-24\n---\nBody text.\n")
    assert "related:\n- server-docker-conventions\n" in text


def test_the_hash_is_stable_across_a_write_and_a_read(repo: FilesystemNoteRepository) -> None:
    note = plain(body="Résumé — 日本語\n\n---\n[[server-docker-conventions]]\n")
    repo.apply([NoteChange(note=note)], MESSAGE)
    stored = repo.get(note.id)
    assert stored is not None
    assert content_hash(stored) == content_hash(note)


def test_reading_tolerates_a_bom_and_crlf_in_the_frontmatter(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path
) -> None:
    note = plain(body="line\r\nnext\r\n")
    repo.apply([NoteChange(note=note)], MESSAGE)
    path = root / "notes/server-ssh-access.md"
    text = path.read_text(newline="")
    path.write_bytes(b"\xef\xbb\xbf" + text.replace("---\n", "---\r\n", 1).replace("\nupdated", "\r\nupdated").encode())
    git.stage(["notes/server-ssh-access.md"])
    git.commit("owner edit")
    assert repo.get(note.id) == note


# -- commits -------------------------------------------------------------------------------------------------------


def test_one_commit_per_apply_with_the_trailers_verbatim(repo: FilesystemNoteRepository, git: GitRepo) -> None:
    revision = repo.apply(
        [NoteChange(note=plain(id=NoteId("note-a"))), NoteChange(note=plain(id=NoteId("note-b")))], MESSAGE
    )
    assert git.head() == revision == repo.head()
    assert messages(git) == [MESSAGE.render()]
    assert git.is_clean()
    repo.apply([NoteChange(note=plain(id=NoteId("note-c")))], MESSAGE)
    assert len(messages(git)) == 2


def test_the_lock_file_is_outside_the_tracked_tree(repo: FilesystemNoteRepository, git: GitRepo, root: Path) -> None:
    repo.apply([NoteChange(note=plain())], MESSAGE)
    assert (root / ".git" / "cerebro-notes.lock").exists()
    assert git.is_clean()
    assert not [p for p in root.rglob("*") if p.name.endswith(".tmp")]


def test_new_files_are_private_for_restricted_notes_and_readable_for_others(
    repo: FilesystemNoteRepository, root: Path
) -> None:
    repo.apply([NoteChange(note=make_note()), NoteChange(note=plain(id=NoteId("open-note")))], MESSAGE)
    restricted = stat.S_IMODE((root / "notes/server-ssh-access.md").stat().st_mode)
    other = stat.S_IMODE((root / "notes/open-note.md").stat().st_mode)
    assert restricted == 0o600
    assert other & 0o600 == 0o600
    assert other & 0o022 == 0


# -- restricted notes ----------------------------------------------------------------------------------------------


def test_restricted_files_hold_only_the_header_in_clear(repo: FilesystemNoteRepository, root: Path) -> None:
    note = make_note(title="Unique-title-marker", summary="Unique summary marker " * 4, body="Unique-body-marker\n")
    repo.apply([NoteChange(note=note)], MESSAGE)
    data = (root / "notes/server-ssh-access.md").read_bytes()
    for marker in (
        b"Unique-title-marker",
        b"Unique summary marker",
        b"Unique-body-marker",
        b"runbook",
        b"infrastructure",
    ):
        assert marker not in data
    assert data.startswith(b"---\nid: server-ssh-access\nsensitivity: restricted\nencryption: fernet-v1\n---\n")
    assert repo.get(note.id) == note


def test_a_plaintext_file_declaring_restricted_is_refused(repo: FilesystemNoteRepository, root: Path) -> None:
    repo.apply([NoteChange(note=plain())], MESSAGE)
    path = root / "notes/server-ssh-access.md"
    path.write_text(path.read_text().replace("sensitivity: internal", "sensitivity: restricted"))
    with pytest.raises(NoteFileError, match="plaintext file declares sensitivity restricted"):
        repo.get(NoteId("server-ssh-access"))
    with pytest.raises(NoteFileError):
        repo.list_notes()


def test_swapping_the_encrypted_payloads_of_two_restricted_notes_is_detected(
    repo: FilesystemNoteRepository, root: Path
) -> None:
    repo.apply(
        [NoteChange(note=make_note(id=NoteId("note-a"))), NoteChange(note=make_note(id=NoteId("note-b")))], MESSAGE
    )
    a, b = root / "notes/note-a.md", root / "notes/note-b.md"
    a_bytes, b_bytes = a.read_bytes(), b.read_bytes()
    a_token, b_token = a_bytes.split(b"---\n")[-1], b_bytes.split(b"---\n")[-1]
    a.write_bytes(a_bytes.replace(a_token, b_token))
    b.write_bytes(b_bytes.replace(b_token, a_token))
    with pytest.raises(NoteFileError, match="cannot be decrypted"):
        repo.get(NoteId("note-a"))
    with pytest.raises(NoteFileError) as info:
        repo.list_notes()
    assert {p for p, _ in info.value.problems} == {"notes/note-a.md", "notes/note-b.md"}


def test_renaming_an_encrypted_file_is_detected(repo: FilesystemNoteRepository, root: Path) -> None:
    repo.apply([NoteChange(note=make_note())], MESSAGE)
    os.rename(root / "notes/server-ssh-access.md", root / "notes/other-name.md")
    with pytest.raises(NoteFileError, match="header id differs from the file name"):
        repo.get(NoteId("other-name"))


def test_an_encrypted_file_whose_inner_note_is_not_restricted_is_refused(
    repo: FilesystemNoteRepository, root: Path, cipher: Cipher
) -> None:
    from cerebro.adapters.outbound.crypto import encode_restricted
    from cerebro.adapters.outbound.filesystem.codec import render_note

    note = plain()
    (root / "notes").mkdir()
    (root / "notes/server-ssh-access.md").write_bytes(encode_restricted(note.id, render_note(note), cipher))
    with pytest.raises(NoteFileError, match="must declare sensitivity restricted"):
        repo.get(note.id)


def test_the_wrong_key_is_a_clear_error(git: GitRepo, root: Path, cipher: Cipher) -> None:
    FilesystemNoteRepository(root, git=git, cipher=cipher).apply([NoteChange(note=make_note())], MESSAGE)
    other = FilesystemNoteRepository(root, git=git, cipher=FernetCipher([generate_key()]))
    with pytest.raises(NoteFileError, match="cannot be decrypted"):
        other.get(make_note().id)


def test_changing_sensitivity_rewrites_the_same_file_in_one_commit(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path
) -> None:
    note = plain(title="Visible-title-marker")
    repo.apply([NoteChange(note=note)], MESSAGE)
    restricted = replace(note, sensitivity=Sensitivity.RESTRICTED)
    repo.apply([NoteChange(note=restricted, expected_hash=content_hash(note))], MESSAGE)
    assert b"Visible-title-marker" not in b"".join(tree_files(root).values())
    assert sorted(tree_files(root)) == ["notes/server-ssh-access.md"]
    assert len(messages(git)) == 2
    repo.apply([NoteChange(note=note, expected_hash=content_hash(restricted))], MESSAGE)
    assert b"Visible-title-marker" in (root / "notes/server-ssh-access.md").read_bytes()
    assert sorted(tree_files(root)) == ["notes/server-ssh-access.md"]
    assert git.is_clean()


# -- writing rules -------------------------------------------------------------------------------------------------


def test_a_type_change_that_would_move_the_file_is_refused(repo: FilesystemNoteRepository, git: GitRepo) -> None:
    note = plain()
    repo.apply([NoteChange(note=note)], MESSAGE)
    head = git.head()
    moved = replace(note, type=HUB, priority=Priority.P1)
    with pytest.raises(InvalidValueError, match="cannot move"):
        repo.apply([NoteChange(note=moved, expected_hash=content_hash(note))], MESSAGE)
    assert git.head() == head
    assert repo.get(note.id) == note


def test_an_update_that_changes_nothing_is_refused(repo: FilesystemNoteRepository, git: GitRepo) -> None:
    note = plain()
    repo.apply([NoteChange(note=note)], MESSAGE)
    head = git.head()
    with pytest.raises(NoteWriteError, match="nothing to commit"):
        repo.apply([NoteChange(note=note, expected_hash=content_hash(note))], MESSAGE)
    assert git.head() == head


def test_a_note_that_would_not_read_back_is_refused_before_touching_the_disk(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path
) -> None:
    for bad in (plain(body="lone surrogate \ud800"), plain(body="x" * (257 * 1024))):
        with pytest.raises(InvalidValueError):
            repo.apply([NoteChange(note=bad)], MESSAGE)
    assert git.head() is None
    assert tree_files(root) == {}


def test_error_messages_never_contain_note_content(repo: FilesystemNoteRepository) -> None:
    secret = "Distinct-secret-marker"
    note = plain(title=secret, body=secret)
    repo.apply([NoteChange(note=note)], MESSAGE)
    with pytest.raises(ContentConflict) as conflict:
        repo.apply([NoteChange(note=replace(note, body=secret + "2"))], MESSAGE)
    assert secret not in str(conflict.value)


# -- atomicity -----------------------------------------------------------------------------------------------------


def seeded(repo: FilesystemNoteRepository, git: GitRepo, root: Path) -> tuple[Note, str | None, dict[str, bytes]]:
    base = plain(id=NoteId("base-note"))
    repo.apply([NoteChange(note=base)], MESSAGE)
    return base, git.head(), tree_files(root)


def failing_changes(base: Note) -> list[NoteChange]:
    edited = replace(base, body="Edited.\n")
    return [
        NoteChange(note=edited, expected_hash=content_hash(base)),
        NoteChange(note=plain(id=NoteId("fresh-note"))),
        NoteChange(note=make_note(id=NoteId("fresh-secret"))),
    ]


def test_a_failure_while_writing_leaves_the_tree_and_head_unchanged(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, head, files = seeded(repo, git, root)
    real = safeio.write_file_atomic
    calls = []

    def flaky(*args: Any, **kwargs: Any) -> None:
        calls.append(args[1])
        if len(calls) == 3:
            raise OSError(28, "No space left on device")
        real(*args, **kwargs)

    monkeypatch.setattr(repository_module, "write_file_atomic", flaky)
    with pytest.raises(NoteWriteError, match="nothing was kept") as info:
        repo.apply(failing_changes(base), MESSAGE)
    assert "No space" not in str(info.value)
    assert git.head() == head
    assert git.is_clean()
    assert tree_files(root) == files


@pytest.mark.parametrize("step", ["stage", "commit"])
def test_a_failure_while_staging_or_committing_rolls_everything_back(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    base, head, files = seeded(repo, git, root)
    real = getattr(GitRepo, step)

    def flaky(self: GitRepo, *args: Any, **kwargs: Any) -> Any:
        if step == "commit":
            raise GitError("git commit failed (exit 1): boom")
        real(self, *args, **kwargs)
        raise GitError("git add failed (exit 1): boom")

    monkeypatch.setattr(GitRepo, step, flaky)
    with pytest.raises(NoteWriteError):
        repo.apply(failing_changes(base), MESSAGE)
    monkeypatch.undo()
    assert git.head() == head
    assert git.is_clean()
    assert tree_files(root) == files


def test_a_draft_at_a_planned_path_survives_a_failed_apply(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _base, head, _ = seeded(repo, git, root)
    draft = root / "notes/fresh-note.md"
    draft.write_text("my draft, not a valid note\n")
    monkeypatch.setattr(GitRepo, "commit", lambda *a, **k: (_ for _ in ()).throw(GitError("git commit failed")))
    with pytest.raises(ContentConflict):  # the file exists, so creating the note conflicts before anything is written
        repo.apply([NoteChange(note=plain(id=NoteId("fresh-note")))], MESSAGE)
    assert draft.read_text() == "my draft, not a valid note\n"
    assert git.head() == head


def test_a_keyboard_interrupt_also_rolls_back(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, head, files = seeded(repo, git, root)

    def interrupted(self: GitRepo, *args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(GitRepo, "commit", interrupted)
    with pytest.raises(KeyboardInterrupt):
        repo.apply(failing_changes(base), MESSAGE)
    monkeypatch.undo()
    assert git.head() == head
    assert tree_files(root) == files


# -- concurrency ---------------------------------------------------------------------------------------------------


def test_concurrent_creates_of_the_same_note_serialise(git: GitRepo, root: Path, cipher: Cipher) -> None:
    results: list[object] = []
    barrier = threading.Barrier(6)

    def worker() -> None:
        mine = FilesystemNoteRepository(root, git=git, cipher=cipher, lock_timeout=30)
        barrier.wait()
        try:
            results.append(mine.apply([NoteChange(note=plain(id=NoteId("contested-note")))], MESSAGE))
        except ContentConflict as exc:
            results.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(isinstance(r, str) for r in results) == 1
    assert sum(isinstance(r, ContentConflict) for r in results) == 5
    assert len(messages(git)) == 1
    assert git.is_clean()


def test_concurrent_applies_of_different_notes_each_make_one_commit(git: GitRepo, root: Path, cipher: Cipher) -> None:
    barrier = threading.Barrier(5)
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        mine = FilesystemNoteRepository(root, git=git, cipher=cipher, lock_timeout=30)
        barrier.wait()
        try:
            mine.apply([NoteChange(note=plain(id=NoteId(f"note-{index}")))], MESSAGE)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(messages(git, 20)) == 5
    assert git.is_clean()
    assert len(FilesystemNoteRepository(root, git=git, cipher=cipher).list_notes()) == 5


def test_a_writer_waiting_too_long_gets_a_clear_error(git: GitRepo, root: Path, cipher: Cipher) -> None:
    from cerebro.adapters.outbound.filesystem.lock import file_lock

    mine = FilesystemNoteRepository(root, git=git, cipher=cipher, lock_timeout=0.2)
    with file_lock(root / ".git" / "cerebro-notes.lock", exclusive=True, timeout=1):
        with pytest.raises(WriterBusyError):
            mine.apply([NoteChange(note=plain())], MESSAGE)
        with pytest.raises(WriterBusyError):
            mine.list_notes()
    mine.apply([NoteChange(note=plain())], MESSAGE)


# -- hostile files -------------------------------------------------------------------------------------------------


def write_note_file(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def good_text(repo: FilesystemNoteRepository, root: Path, note: Note) -> str:
    repo.apply([NoteChange(note=note)], MESSAGE)
    return (root / f"{'notes'}/{note.id.value}.md").read_text()


def test_a_symlinked_note_file_is_refused(repo: FilesystemNoteRepository, root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text(good_text(repo, root, plain()))
    (root / "notes/linked-note.md").symlink_to(outside)
    with pytest.raises(NoteFileError, match="cannot be read"):
        repo.get(NoteId("linked-note"))
    with pytest.raises(NoteFileError) as info:
        repo.list_notes()
    assert [p for p, _ in info.value.problems] == ["notes/linked-note.md"]


def test_a_symlinked_folder_is_refused(repo: FilesystemNoteRepository, root: Path, tmp_path: Path) -> None:
    real = tmp_path / "elsewhere"
    real.mkdir()
    (root / "notes").symlink_to(real)
    with pytest.raises(NoteFileError, match="not a real directory"):
        repo.list_notes()
    with pytest.raises(NoteFileError):
        repo.get(NoteId("anything"))
    with pytest.raises(NoteFileError):
        repo.apply([NoteChange(note=plain())], MESSAGE)
    assert list(real.iterdir()) == []


def test_a_symlink_at_the_target_of_a_write_is_refused(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path, tmp_path: Path
) -> None:
    (root / "notes").mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    (root / "notes/server-ssh-access.md").symlink_to(victim)
    with pytest.raises(ContentConflict):
        repo.apply([NoteChange(note=plain())], MESSAGE)
    assert victim.read_text() == "precious"
    assert git.head() is None


def test_an_oversized_file_is_refused_without_being_parsed(repo: FilesystemNoteRepository, root: Path) -> None:
    write_note_file(root, "notes/huge-note.md", "---\n" + "x" * (600 * 1024))
    with pytest.raises(NoteFileError, match="too large"):
        repo.get(NoteId("huge-note"))


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("no frontmatter\n", "does not start"),
        ("---\nid: a: b: c\n---\n", "invalid frontmatter"),
        ("---\nid: &a x\nitle: *a\n---\n", "anchors"),
        ("---\n!!python/object/apply:os.system [x]\n---\n", "tags"),
        ("---\nid: broken-note\nid: broken-note\n---\n", "duplicate"),
        ("---\nid: broken-note\n---\n", "missing_field"),
    ],
)
def test_malformed_note_files_name_the_path_and_a_fixed_reason(
    repo: FilesystemNoteRepository, root: Path, text: str, reason: str
) -> None:
    write_note_file(root, "notes/broken-note.md", text)
    with pytest.raises(NoteFileError, match=reason) as info:
        repo.get(NoteId("broken-note"))
    assert info.value.path == "notes/broken-note.md"


def test_a_note_in_the_wrong_folder_or_with_a_different_id_is_refused(
    repo: FilesystemNoteRepository, root: Path
) -> None:
    text = good_text(repo, root, plain())
    write_note_file(root, "core/server-ssh-access.md", text)
    (root / "notes/server-ssh-access.md").unlink()
    with pytest.raises(NoteFileError, match=r"belongs in notes/, not core/"):
        repo.get(NoteId("server-ssh-access"))
    write_note_file(root, "notes/other-id.md", text)
    with pytest.raises(NoteFileError, match="differs from the file name"):
        repo.get(NoteId("other-id"))


def test_the_same_id_in_two_folders_is_refused(repo: FilesystemNoteRepository, root: Path) -> None:
    text = good_text(repo, root, plain())
    write_note_file(root, "core/server-ssh-access.md", text)
    with pytest.raises(NoteFileError, match="several folders"):
        repo.get(NoteId("server-ssh-access"))
    with pytest.raises(ContentConflict):
        repo.apply([NoteChange(note=plain())], MESSAGE)


def test_listing_reports_every_bad_file_and_ignores_the_irrelevant_ones(
    repo: FilesystemNoteRepository, root: Path
) -> None:
    good_text(repo, root, plain())
    write_note_file(root, "notes/.hidden.md", "not parsed")
    write_note_file(root, "notes/readme.txt", "not parsed")
    write_note_file(root, "notes/Bad Name.md", "x")
    write_note_file(root, "notes/broken-note.md", "x")
    (root / "notes/subfolder").mkdir()
    with pytest.raises(NoteFileError) as info:
        repo.list_notes()
    assert sorted(p for p, _ in info.value.problems) == [
        "notes/Bad Name.md",
        "notes/broken-note.md",
        "notes/subfolder",
    ]


def test_listing_without_any_folder_is_empty(repo: FilesystemNoteRepository) -> None:
    assert repo.list_notes() == ()
    assert repo.list_refs() == ()


def test_hostile_file_names_cannot_inject_control_characters_into_errors(
    repo: FilesystemNoteRepository, root: Path
) -> None:
    write_note_file(root, "notes/evil\nname.md", "x")
    with pytest.raises(NoteFileError) as info:
        repo.list_notes()
    assert "\n" not in str(info.value)


# -- vocabulary ----------------------------------------------------------------------------------------------------


def write_schema(root: Path, areas: str, types: str) -> None:
    (root / "schema").mkdir(exist_ok=True)
    (root / "schema/areas.yaml").write_text(areas)
    (root / "schema/types.yaml").write_text(types)


def test_the_vocabulary_is_loaded_from_the_schema_files(root: Path, git: GitRepo) -> None:
    write_schema(root, "areas:\n  - infrastructure\n  - finance\n", "types:\n  - runbook\n  - guide\n  - hub\n")
    vocabulary = load_vocabulary(root)
    assert vocabulary.has_area(Area("finance"))
    assert vocabulary.has_type(NoteType("runbook"))
    assert vocabulary.has_type(CORE)
    assert vocabulary.user_types == {NoteType("runbook"), NoteType("guide")}


@pytest.mark.parametrize(
    ("areas", "types"),
    [
        ("areas: [a]\nextra: 1\n", "types: []\n"),
        ("areas: [a, a]\n", "types: []\n"),
        ("areas: [Not Kebab]\n", "types: []\n"),
        ("areas: a\n", "types: []\n"),
        ("areas: [a]\n", "- list\n"),
        ("areas: [&x a, *x]\n", "types: []\n"),
        ("areas: [a]\n", "types: [1, 2]\nmore: x\n"),
    ],
)
def test_a_malformed_vocabulary_is_refused(root: Path, areas: str, types: str) -> None:
    write_schema(root, areas, types)
    with pytest.raises(VocabularyError):
        load_vocabulary(root)


def test_a_missing_or_symlinked_schema_file_is_refused(root: Path, tmp_path: Path) -> None:
    with pytest.raises(VocabularyError, match="missing"):
        load_vocabulary(root)
    write_schema(root, "areas: [a]\n", "types: []\n")
    (root / "schema/types.yaml").unlink()
    elsewhere = tmp_path / "types.yaml"
    elsewhere.write_text("types: []\n")
    (root / "schema/types.yaml").symlink_to(elsewhere)
    with pytest.raises(VocabularyError, match="symlink"):
        load_vocabulary(root)


def test_the_repository_rejects_a_missing_root(tmp_path: Path, git: GitRepo, cipher: Cipher) -> None:
    with pytest.raises(ValueError, match="not a directory"):
        FilesystemNoteRepository(tmp_path / "nope", git=git, cipher=cipher)


# -- unclean state, post-commit failures, housekeeping -------------------------------------------------------------


def committed_base(repo: FilesystemNoteRepository, git: GitRepo) -> tuple[Note, str | None]:
    base = plain(id=NoteId("base-note"))
    repo.apply([NoteChange(note=base)], MESSAGE)
    return base, git.head()


def fail_commit(*args: Any, **kwargs: Any) -> Any:
    raise GitError("boom")


def test_an_untracked_hand_made_note_at_an_update_path_is_refused(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path
) -> None:
    from cerebro.adapters.outbound.filesystem.codec import render_note

    note = plain(id=NoteId("hand-made"))
    (root / "notes").mkdir()
    (root / "notes/hand-made.md").write_bytes(render_note(note))
    head = git.head()
    with pytest.raises(NoteWriteError, match="untracked or has uncommitted"):
        repo.apply([NoteChange(note=replace(note, body="New.\n"), expected_hash=content_hash(note))], MESSAGE)
    assert git.head() == head
    assert (root / "notes/hand-made.md").read_bytes() == render_note(note)


def test_a_staged_unrelated_file_refuses_the_apply_and_is_never_committed(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path
) -> None:
    _, head = committed_base(repo, git)
    (root / "unrelated.txt").write_text("secret draft\n")
    git.stage(["unrelated.txt"])
    with pytest.raises(NoteWriteError, match="staged changes"):
        repo.apply([NoteChange(note=plain(id=NoteId("other-note")))], MESSAGE)
    assert git.head() == head
    assert repo.get(NoteId("other-note")) is None


def test_an_uncommitted_edit_of_a_tracked_note_refuses_the_update(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path
) -> None:
    base, head = committed_base(repo, git)
    path = root / "notes/base-note.md"
    path.write_text(path.read_text() + "hand edit\n")
    edited = replace(base, body=base.body + "hand edit\n")
    assert content_hash(edited) == content_hash(parse_note_bytes(path.read_bytes()))
    change = NoteChange(note=replace(edited, summary=edited.summary + " x"), expected_hash=content_hash(edited))
    with pytest.raises(NoteWriteError, match="uncommitted"):
        repo.apply([change], MESSAGE)
    assert git.head() == head
    assert path.read_text().endswith("hand edit\n")


def test_a_note_deleted_by_hand_and_created_again_is_refused(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path
) -> None:
    base, head = committed_base(repo, git)
    (root / "notes/base-note.md").unlink()
    with pytest.raises(NoteWriteError, match="already known to git"):
        repo.apply([NoteChange(note=replace(base, body="Again.\n"))], MESSAGE)
    assert git.head() == head
    assert not (root / "notes/base-note.md").exists()


# -- only committed note content is served ---------------------------------------------------------------------


UNCOMMITTED = repository_module.UNCOMMITTED_REASON


def test_reads_refuse_every_uncommitted_note_file_and_name_it(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path
) -> None:
    from cerebro.adapters.outbound.filesystem.codec import render_note

    base, _ = committed_base(repo, git)
    repo.apply([NoteChange(note=plain(id=NoteId("gone-note")))], MESSAGE)
    repo.apply([NoteChange(note=plain(id=NoteId("staged-note")))], MESSAGE)
    (root / "notes/base-note.md").write_bytes(render_note(replace(base, title="Edited by hand")))
    (root / "notes/gone-note.md").unlink()
    (root / "notes/staged-note.md").write_bytes(render_note(plain(id=NoteId("staged-note"), body="Staged.\n")))
    git.stage(["notes/staged-note.md"])
    (root / "notes/orphan-note.md").write_bytes(render_note(plain(id=NoteId("orphan-note"))))
    with pytest.raises(NoteFileError) as listed:
        repo.list_notes()
    assert listed.value.problems == tuple(
        (f"notes/{name}.md", UNCOMMITTED) for name in ("base-note", "gone-note", "orphan-note", "staged-note")
    )
    for name in ("base-note", "orphan-note", "staged-note"):
        with pytest.raises(NoteFileError) as read:
            repo.get(NoteId(name))
        assert read.value.problems == ((f"notes/{name}.md", UNCOMMITTED),)
    assert repo.get(NoteId("gone-note")) is None


def test_an_ignored_note_file_is_refused(repo: FilesystemNoteRepository, git: GitRepo, root: Path) -> None:
    from cerebro.adapters.outbound.filesystem.codec import render_note

    committed_base(repo, git)
    (root / ".git/info").mkdir(exist_ok=True)
    (root / ".git/info/exclude").write_text("hidden-note.md\n")
    (root / "notes/hidden-note.md").write_bytes(render_note(plain(id=NoteId("hidden-note"))))
    with pytest.raises(NoteFileError, match="differs from the last commit"):
        repo.list_notes()
    with pytest.raises(NoteFileError, match="differs from the last commit"):
        repo.get(NoteId("hidden-note"))


def test_files_the_listing_never_reads_do_not_block_reads(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path
) -> None:
    base, _ = committed_base(repo, git)
    (root / "README.md").write_text("draft\n")
    (root / "schema").mkdir(exist_ok=True)
    (root / "schema/draft.yaml").write_text("draft: true\n")
    (root / "notes/.base-note.md.0123456789abcdef.tmp").write_text("left by a crash\n")
    (root / "notes/scratch.txt").write_text("draft\n")
    assert repo.list_notes() == (base,)
    assert repo.get(base.id) == base


def test_a_note_left_staged_by_an_interrupted_write_names_the_path_and_the_fix(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path
) -> None:
    from cerebro.adapters.outbound.filesystem.codec import render_note

    _, head = committed_base(repo, git)
    (root / "notes/half-done.md").write_bytes(render_note(plain(id=NoteId("half-done"))))
    git.stage(["notes/half-done.md"])
    with pytest.raises(NoteWriteError, match=r"staged changes \('notes/half-done.md'\).*git restore --staged"):
        repo.apply([NoteChange(note=plain(id=NoteId("other-note")))], MESSAGE)
    assert git.head() == head
    assert (root / "notes/half-done.md").exists()  # never discarded automatically


def test_a_leftover_index_lock_refuses_the_write_with_a_hint(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path
) -> None:
    _, head = committed_base(repo, git)
    (root / ".git/index.lock").write_text("")
    with pytest.raises(NoteWriteError, match=r"stale \.git/index\.lock.*remove that file"):
        repo.apply([NoteChange(note=plain(id=NoteId("other-note")))], MESSAGE)
    assert git.head() == head
    assert (root / ".git/index.lock").exists()


def test_a_commit_failing_on_an_index_lock_mentions_it(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, head = committed_base(repo, git)
    real_stage = GitRepo.stage

    def stage_then_lock(self: GitRepo, paths: Any) -> None:
        real_stage(self, paths)
        (root / ".git/index.lock").write_text("")  # another git command started meanwhile

    monkeypatch.setattr(GitRepo, "stage", stage_then_lock)
    with pytest.raises(NoteWriteError, match=r"rollback was incomplete.*\(.*stale \.git/index\.lock"):
        repo.apply([NoteChange(note=plain(id=NoteId("other-note")))], MESSAGE)
    assert git.head() == head
    (root / ".git/index.lock").unlink()
    with pytest.raises(NoteFileError, match="differs from the last commit"):
        repo.list_notes()  # the half-written note is refused, never served, until the owner resolves it


def test_a_failure_after_the_commit_does_not_roll_back_and_returns_the_revision(
    repo: FilesystemNoteRepository, git: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, head = committed_base(repo, git)
    real_commit = GitRepo.commit

    def commit_then_raise(self: GitRepo, *args: Any, **kwargs: Any) -> str:
        real_commit(self, *args, **kwargs)
        raise GitError("raised after the commit was made")

    monkeypatch.setattr(GitRepo, "commit", commit_then_raise)
    revision = repo.apply([NoteChange(note=plain(id=NoteId("after-commit")))], MESSAGE)
    monkeypatch.undo()
    assert revision == git.head() != head
    assert repo.get(NoteId("after-commit")) is not None
    assert git.is_clean()


def test_a_failure_after_the_commit_with_an_unreadable_head_reports_unknown_state(
    repo: FilesystemNoteRepository, git: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    committed_base(repo, git)
    real_head, real_commit = GitRepo.head, GitRepo.commit
    state = {"after_commit": False}

    def commit(self: GitRepo, *args: Any, **kwargs: Any) -> str:
        real_commit(self, *args, **kwargs)
        state["after_commit"] = True
        raise GitError("raised after the commit")

    def head(self: GitRepo) -> str | None:
        if state["after_commit"]:
            raise GitError("unreadable")
        return real_head(self)

    monkeypatch.setattr(GitRepo, "commit", commit)
    monkeypatch.setattr(GitRepo, "head", head)
    with pytest.raises(NoteWriteError, match="unknown"):
        repo.apply([NoteChange(note=plain(id=NoteId("after-commit")))], MESSAGE)
    monkeypatch.undo()
    assert repo.get(NoteId("after-commit")) is not None  # committed, and never rolled back


def outside_commit(root: Path, name: str = "outside.txt") -> None:
    (root / name).write_text("made by someone else\n")
    raw_git(root, "add", name)
    raw_git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "outside commit")


def test_a_foreign_commit_during_a_failed_stage_is_never_returned_as_ours(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    committed_base(repo, git)

    def stage_then_fail(self: GitRepo, paths: Any) -> None:
        outside_commit(root)
        raise GitError("stage failed")

    monkeypatch.setattr(GitRepo, "stage", stage_then_fail)
    with pytest.raises(NoteWriteError, match="moved by someone else"):
        repo.apply([NoteChange(note=plain(id=NoteId("mine-note")))], MESSAGE)
    monkeypatch.undo()
    assert messages(git, 1) == ["outside commit"]


def test_a_foreign_commit_while_commit_fails_is_never_returned_as_ours(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    committed_base(repo, git)

    def commit_fails_after_outsider(self: GitRepo, *args: Any, **kwargs: Any) -> str:
        outside_commit(root)
        raise GitError("commit failed")

    monkeypatch.setattr(GitRepo, "commit", commit_fails_after_outsider)
    with pytest.raises(NoteWriteError, match="moved by someone else"):
        repo.apply([NoteChange(note=plain(id=NoteId("mine-note")))], MESSAGE)


def committed_gitignore(git: GitRepo, root: Path, pattern: str) -> None:
    (root / ".gitignore").write_text(pattern + "\n")
    raw_git(root, "add", ".gitignore")
    raw_git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "ignore")


@pytest.mark.parametrize("note", [plain(), make_note()], ids=["plain", "restricted"])
def test_a_created_file_at_an_ignored_path_is_removed_on_failure(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path, note: Note
) -> None:
    committed_gitignore(git, root, "notes/*")
    head = git.head()
    with pytest.raises(NoteWriteError):  # ``git add`` refuses ignored paths
        repo.apply([NoteChange(note=note)], MESSAGE)
    assert git.head() == head
    assert not (root / f"notes/{note.id.value}.md").exists()
    assert not (root / "notes").exists() or not list((root / "notes").iterdir())


def test_an_interrupt_is_not_replaced_when_the_rollback_also_fails(
    repo: FilesystemNoteRepository, git: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    committed_base(repo, git)

    def interrupted(self: GitRepo, *args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(GitRepo, "commit", interrupted)
    monkeypatch.setattr(GitRepo, "discard_changes", fail_commit)
    with pytest.raises(KeyboardInterrupt):
        repo.apply([NoteChange(note=plain(id=NoteId("mine-note")))], MESSAGE)


def test_stale_temp_files_of_another_user_are_not_removed(
    repo: FilesystemNoteRepository, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (root / "notes").mkdir()
    old = root / "notes/.a-note.md.0123456789abcdef.tmp"
    old.write_text("x")
    os.utime(old, (1, 1))
    monkeypatch.setattr(os, "getuid", lambda: os.stat(old).st_uid + 1)
    repo.apply([NoteChange(note=plain())], MESSAGE)
    assert old.exists()


def test_a_rollback_puts_restricted_files_back_to_0600(
    repo: FilesystemNoteRepository, git: GitRepo, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = make_note()
    repo.apply([NoteChange(note=note)], MESSAGE)
    path = root / "notes/server-ssh-access.md"
    path.chmod(0o644)  # what a restore under a permissive umask would leave behind
    monkeypatch.setattr(GitRepo, "commit", fail_commit)
    with pytest.raises(NoteWriteError):
        repo.apply([NoteChange(note=replace(note, body="Edited.\n"), expected_hash=content_hash(note))], MESSAGE)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert repo.get(note.id) == note


def test_stale_temp_files_are_removed_and_fresh_ones_kept(repo: FilesystemNoteRepository, root: Path) -> None:
    (root / "notes").mkdir()
    old = root / "notes/.a-note.md.0123456789abcdef.tmp"
    fresh = root / "notes/.b-note.md.0123456789abcdef.tmp"
    other = root / "notes/.keep-me.tmp"
    for p in (old, fresh, other):
        p.write_text("x")
    os.utime(old, (1, 1))
    os.utime(other, (1, 1))
    repo.apply([NoteChange(note=plain())], MESSAGE)
    assert not old.exists()
    assert fresh.exists()
    assert other.exists()


def test_a_git_file_instead_of_a_directory_is_a_clear_error(tmp_path: Path, git: GitRepo, cipher: Cipher) -> None:
    other = tmp_path / "worktree"
    other.mkdir()
    (other / ".git").write_text("gitdir: elsewhere\n")
    with pytest.raises(ValueError, match=r"real \.git directory"):
        FilesystemNoteRepository(other, git=git, cipher=cipher)
