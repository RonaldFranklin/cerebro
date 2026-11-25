"""Rollback ownership, directory and encoding edge cases of the git adapter (real ``git``, local only)."""

import os
import shutil
from pathlib import Path

import pytest

from cerebro.adapters.outbound.git import GitError, GitRepo, scrub
from tests.integration.test_git import IDENTITY, make_repo, raw_git, write

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the git executable is not installed")


def test_a_preexisting_draft_at_a_planned_path_survives_a_failed_apply(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    write(root, "base.md")
    repo.stage(["base.md"])
    repo.commit("base")
    write(root, "notes/draft.md", "my uncommitted draft\n")
    planned = ["notes/draft.md", "notes/fresh.md"]
    before = repo.snapshot_existing(planned)
    assert before == frozenset({"notes/draft.md"})
    write(root, "notes/draft.md", "overwritten by the apply\n")
    write(root, "notes/fresh.md")
    repo.stage(planned)
    repo.discard_changes(created=planned, modified=[], preexisting=before)
    assert (root / "notes/draft.md").exists()
    assert not (root / "notes/fresh.md").exists()


def test_snapshot_sees_staged_files_too(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    write(root, "a.md")
    repo.stage(["a.md"])
    (root / "a.md").unlink()
    assert repo.snapshot_existing(["a.md", "b.md"]) == frozenset({"a.md"})


def test_a_directory_tracked_in_head_is_refused_even_when_absent_on_disk(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    write(root, "n/a.md")
    write(root, "n/b.md")
    repo.stage(["n/a.md", "n/b.md"])
    head = repo.commit("base")
    shutil.rmtree(root / "n")
    with pytest.raises(GitError):
        repo.discard_changes(created=["n"], modified=[])
    with pytest.raises(GitError):
        repo.discard_changes(created=[], modified=["n"])
    assert repo.head() == head
    assert not (root / "n").exists()
    assert raw_git(root, "ls-tree", "-r", "--name-only", "HEAD").split() == ["n/a.md", "n/b.md"]


def test_modified_must_be_tracked_and_created_must_not_be(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    write(root, "a.md")
    repo.stage(["a.md"])
    repo.commit("base")
    with pytest.raises(GitError):
        repo.discard_changes(created=[], modified=["unknown.md"])
    with pytest.raises(GitError):
        repo.discard_changes(created=["a.md"], modified=[])


def test_non_utf8_paths_are_rejected(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    bad = os.fsdecode(b"caf\xe9.md")
    with pytest.raises(GitError, match="UTF-8"):
        repo.stage([bad])
    with pytest.raises(GitError, match="UTF-8"):
        repo.snapshot_existing([bad])


def test_dot_git_must_be_a_real_directory(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (root / ".git").write_text(f"gitdir: {tmp_path / 'elsewhere'}\n")  # a gitfile
    repo = GitRepo(root, identity=IDENTITY)
    with pytest.raises(GitError, match="real directory"):
        repo.head()


@pytest.mark.parametrize("name", ["secret-salary.md", "it's-private.md"])
def test_scrub_removes_file_names_with_apostrophes(name: str) -> None:
    cleaned = scrub(f"fatal: could not open {name} for reading", secrets=[name])
    assert "salary" not in cleaned
    assert "private" not in cleaned


@pytest.mark.parametrize("name", ["secret-salary.md", "it's-private.md", "dir/it's-private.md"])
def test_real_errors_do_not_name_the_files(tmp_path: Path, name: str) -> None:
    repo = make_repo(tmp_path)
    with pytest.raises(GitError) as caught:
        repo.stage([name])  # does not exist: git complains about the pathspec
    assert "salary" not in str(caught.value)
    assert "private" not in str(caught.value)
