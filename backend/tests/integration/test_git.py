"""Integration tests of the git adapter against the real ``git`` CLI (local repositories only, no network)."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from cerebro.adapters.outbound.git import GitError, GitIdentity, GitRepo, GitVersionControl, scrub
from cerebro.application.ports import VersionControl
from cerebro.domain.commit_message import CommitMessage

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the git executable is not installed")

IDENTITY = GitIdentity(name="Cerebro Writer", email="writer@example.invalid")


def make_repo(tmp_path: Path, *, name: str = "repo", identity: GitIdentity = IDENTITY) -> GitRepo:
    root = tmp_path / name
    root.mkdir()
    repo = GitRepo(root, identity=identity)
    repo.init("main")
    return repo


def write(repo_root: Path, rel: str, text: str = "x\n") -> None:
    target = repo_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def message(subject: str = "Add note alpha") -> CommitMessage:
    return CommitMessage(
        subject=subject,
        proposal_id="proposal-1",
        proposed_by="claude-code",
        curated_by="curator/default",
        approved_by="owner",
    )


def raw_git(root: Path, *args: str) -> str:
    env = {"PATH": os.environ["PATH"], "HOME": str(root), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env, check=True)
    return done.stdout


def test_init_commit_head_round_trip(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    assert repo.head() is None
    assert repo.is_clean()
    assert repo.log() == []
    write(root, "notes/a.md")
    assert not repo.is_clean()
    repo.stage(["notes/a.md"])
    first = repo.commit(message().render())
    assert repo.head() == first
    assert len(first) == 40
    assert repo.is_clean()
    write(root, "notes/a.md", "changed\n")
    repo.add(["notes/a.md"])
    second = repo.commit("second")
    assert repo.head() == second != first
    assert repo.log(1) == ["second"]
    assert raw_git(root, "symbolic-ref", "--short", "HEAD").strip() == "main"


def test_commit_message_with_trailers_is_read_back_verbatim(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(tmp_path / "repo", "a.md")
    repo.stage(["a.md"])
    repo.commit(message().render())
    assert repo.log(1) == [message().render()]
    trailers = raw_git(tmp_path / "repo", "log", "-1", "--format=%(trailers:key=Approved-by,valueonly)").strip()
    assert trailers == "owner"


def test_commit_uses_the_configured_identity_and_ignores_config_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".gitconfig").write_text("[user]\n\tname = Global Name\n\temail = global@example.invalid\n")
    other = tmp_path / "other.gitconfig"
    other.write_text("[user]\n\tname = Env Config\n\temail = envconfig@example.invalid\n")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(other))
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Env Person")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "env@example.invalid")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    repo = make_repo(tmp_path)
    write(tmp_path / "repo", "a.md")
    repo.stage(["a.md"])
    repo.commit("m")
    who = raw_git(tmp_path / "repo", "log", "-1", "--format=%an <%ae> | %cn <%ce>").strip()
    assert who == "Cerebro Writer <writer@example.invalid> | Cerebro Writer <writer@example.invalid>"
    assert not (tmp_path / "elsewhere").exists()


def test_repository_signing_and_identity_config_do_not_apply(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    raw_git(root, "config", "commit.gpgsign", "true")
    raw_git(root, "config", "user.name", "Repo Person")
    write(root, "a.md")
    repo.stage(["a.md"])
    repo.commit("m")  # would fail on signing if the repo setting were honoured
    assert raw_git(root, "log", "-1", "--format=%an").strip() == "Cerebro Writer"


def test_hooks_do_not_run(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    marker = tmp_path / "hook-ran"
    for name in ("pre-commit", "commit-msg", "post-commit", "pre-push"):
        hook = root / ".git" / "hooks" / name
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    write(root, "a.md")
    repo.stage(["a.md"])
    repo.commit("m")
    assert not marker.exists()


def test_it_never_walks_up_to_a_parent_repository(tmp_path: Path) -> None:
    parent = make_repo(tmp_path, name="parent")
    child_root = tmp_path / "parent" / "child"
    child_root.mkdir()
    child = GitRepo(child_root, identity=IDENTITY)
    write(tmp_path / "parent", "p.md")
    parent.stage(["p.md"])
    parent.commit("parent commit")
    with pytest.raises(GitError):  # the parent repository is not used for a directory without its own .git
        child.head()


@pytest.mark.parametrize(
    "bad", ["../outside.md", "a/../../outside.md", "/etc/passwd", "", "-rf", ".git/config", "a/.GIT/x"]
)
def test_unsafe_paths_are_rejected(tmp_path: Path, bad: str) -> None:
    repo = make_repo(tmp_path)
    with pytest.raises(GitError):
        repo.stage([bad])
    with pytest.raises(GitError):
        repo.discard_changes([bad])


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret\n")
    (root / "link").symlink_to(outside)
    (root / "filelink.md").symlink_to(outside / "secret.md")
    with pytest.raises(GitError):
        repo.stage(["link/secret.md"])
    with pytest.raises(GitError):
        repo.stage(["filelink.md"])


def test_paths_are_literal_not_globs_and_not_options(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    write(root, "a.md")
    write(root, "b.md")
    with pytest.raises(GitError):
        repo.stage(["*.md"])  # no file is literally named "*.md": nothing is staged
    assert raw_git(root, "diff", "--cached", "--name-only") == ""


def test_timeout_is_enforced(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    slow = tmp_path / "slow-git"
    slow.write_text("#!/bin/sh\nsleep 30\n")
    slow.chmod(0o755)
    repo = GitRepo(root, identity=IDENTITY, git_executable=str(slow), timeout=0.3)
    with pytest.raises(GitError, match="timed out"):
        repo.head()


def test_output_is_capped(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    noisy = tmp_path / "noisy-git"
    noisy.write_text("#!/bin/sh\nhead -c 3000000 /dev/zero | tr '\\0' 'a'\n")
    noisy.chmod(0o755)
    repo = GitRepo(root, identity=IDENTITY, git_executable=str(noisy))
    head = repo.head()
    assert head is not None
    assert len(head) == 1024 * 1024


def test_rollback_restores_head_and_removes_new_files(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    write(root, "a.md", "one\n")
    write(root, "b.md", "keep\n")
    repo.stage(["a.md", "b.md"])
    head = repo.commit("base")
    write(root, "a.md", "modified\n")  # tracked, modified
    (root / "b.md").unlink()  # tracked, deleted
    write(root, "notes/new.md")  # new and staged
    write(root, "notes/untracked.md")  # new, never staged
    repo.stage(["a.md", "notes/new.md"])
    repo.discard_changes(["notes/new.md", "notes/untracked.md"])
    assert repo.head() == head
    assert (root / "a.md").read_text() == "one\n"
    assert (root / "b.md").read_text() == "keep\n"
    assert not (root / "notes/new.md").exists()
    assert not (root / "notes/untracked.md").exists()
    assert repo.is_clean()


def test_rollback_before_the_first_commit(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    write(root, "a.md")
    repo.stage(["a.md"])
    repo.discard_changes(["a.md"])
    assert not (root / "a.md").exists()
    assert repo.head() is None
    assert repo.is_clean()


def test_committing_nothing_is_an_error_without_the_message(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    with pytest.raises(GitError) as caught:
        repo.commit("TOP-SECRET-BODY")
    assert "TOP-SECRET-BODY" not in str(caught.value)
    with pytest.raises(GitError):
        repo.commit("  ")


def bare_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--quiet", "--initial-branch=main", str(remote)], check=True)
    return remote


def test_push_to_a_local_bare_remote(tmp_path: Path) -> None:
    remote = bare_remote(tmp_path)
    repo = make_repo(tmp_path)
    write(tmp_path / "repo", "a.md")
    repo.stage(["a.md"])
    head = repo.commit("m")
    vcs: VersionControl = GitVersionControl(repo, remote=str(remote), branch="main")
    vcs.push()
    assert raw_git(remote, "rev-parse", "main").strip() == head


def test_push_without_a_remote_is_a_no_op(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    GitVersionControl(repo, remote=None, branch="main").push()
    GitVersionControl(repo, remote="", branch="main").push()


def test_push_failure_is_a_redacted_git_error(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(tmp_path / "repo", "a.md")
    repo.stage(["a.md"])
    repo.commit("m")
    url = "https://deploy-user:s3cr3t-token@127.0.0.1:9/owner/content.git"
    with pytest.raises(GitError) as caught:
        GitVersionControl(repo, remote=url, branch="main", push_timeout=20).push()
    text = str(caught.value)
    assert "s3cr3t-token" not in text
    assert "deploy-user" not in text
    assert str(tmp_path) not in text


@pytest.mark.parametrize("remote", ["--upload-pack=evil", "ext::sh -c id", "a b"])
def test_dangerous_remotes_are_refused(tmp_path: Path, remote: str) -> None:
    repo = make_repo(tmp_path)
    with pytest.raises(GitError):
        GitVersionControl(repo, remote=remote, branch="main").push()


@pytest.mark.parametrize("branch", ["--delete", "a..b", "x.lock", "", "a b"])
def test_invalid_branches_are_refused(tmp_path: Path, branch: str) -> None:
    repo = make_repo(tmp_path)
    with pytest.raises(GitError):
        repo.init(branch)


def test_scrub_removes_credentials_paths_and_secrets() -> None:
    text = "fatal: unable to access 'https://user:pw@host.example/x.git/': failed in /home/me/secret/dir"
    cleaned = scrub(text, secrets=["host.example"], root=Path("/home/me/secret"))
    assert "pw" not in cleaned
    assert "user" not in cleaned
    assert "host.example" not in cleaned
    assert "/home/me" not in cleaned
    assert len(scrub("x" * 5000)) == 500


def test_missing_directory_or_executable_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        GitRepo(tmp_path / "missing", identity=IDENTITY)
    with pytest.raises(GitError):
        GitRepo(tmp_path, identity=IDENTITY, git_executable="definitely-not-git-xyz")


def test_invalid_identity_is_refused() -> None:
    with pytest.raises(GitError):
        GitIdentity(name="a\nb", email="x@y")
    with pytest.raises(GitError):
        GitIdentity(name="ok", email="<x@y>")
