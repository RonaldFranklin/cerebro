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
from cerebro.adapters.outbound.git import GitError, GitRepo
from cerebro.application.ports import Cipher, NoteChange, NoteRepository
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.errors import ContentConflict, InvalidValueError
from cerebro.domain.note import Note, NoteId
from cerebro.domain.vocabulary import CORE, HUB, Area, NoteType
from tests.contract.notes_contract import MESSAGE, NoteRepositoryContract
from tests.integration.test_git import make_repo
from tests.unit.domain.builders import make_note

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


def test_reading_tolerates_a_bom_and_crlf_in_the_frontmatter(repo: FilesystemNoteRepository, root: Path) -> None:
    note = plain(body="line\r\nnext\r\n")
    repo.apply([NoteChange(note=note)], MESSAGE)
    path = root / "notes/server-ssh-access.md"
    text = path.read_text(newline="")
    path.write_bytes(b"\xef\xbb\xbf" + text.replace("---\n", "---\r\n", 1).replace("\nupdated", "\r\nupdated").encode())
    assert repo.get(note.id) == note


# -- commits -------------------------------------------------------------------------------------------------------


def test_one_commit_per_apply_with_the_trailers_verbatim(repo: FilesystemNoteRepository, git: GitRepo) -> None:
    revision = repo.apply(
        [NoteChange(note=plain(id=NoteId("note-a"))), NoteChange(note=plain(id=NoteId("note-b")))], MESSAGE
    )
    assert git.head() == revision == repo.head()
    assert git.log() == [MESSAGE.render()]
    assert git.is_clean()
    repo.apply([NoteChange(note=plain(id=NoteId("note-c")))], MESSAGE)
    assert len(git.log()) == 2


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
    assert len(git.log()) == 2
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
    assert len(git.log()) == 1
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
    assert len(git.log(20)) == 5
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
        ("areas: []\n", "types: []\n"),
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
