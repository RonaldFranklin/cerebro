"""Integration tests of the git adapter against the real ``git`` CLI (local repositories only, no network)."""

import os
import shutil
import stat
import subprocess
import time
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
    "bad",
    [
        "../outside.md",
        "a/../../outside.md",
        "/etc/passwd",
        "",
        "-rf",
        ".git/config",
        "a/.GIT/x",
        ".",
        "./",
        "notes/..",
        ".gitattributes",
        "n/.gitmodules",
        ".gitignore",
    ],
)
def test_unsafe_paths_are_rejected(tmp_path: Path, bad: str) -> None:
    repo = make_repo(tmp_path)
    with pytest.raises(GitError):
        repo.stage([bad])
    with pytest.raises(GitError):
        repo.discard_changes(created=[bad], modified=[])


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
    (root / ".git").mkdir()
    slow = tmp_path / "slow-git"
    slow.write_text("#!/bin/sh\nsleep 30\n")
    slow.chmod(0o755)
    repo = GitRepo(root, identity=IDENTITY, git_executable=str(slow), timeout=0.3)
    with pytest.raises(GitError, match="timed out"):
        repo.head()


def test_output_is_capped(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    noisy = tmp_path / "noisy-git"
    noisy.write_text("#!/bin/sh\nhead -c 3000000 /dev/zero | tr '\\0' 'a'\n")
    noisy.chmod(0o755)
    repo = GitRepo(root, identity=IDENTITY, git_executable=str(noisy))
    with pytest.raises(GitError, match="size limit"):
        repo.head()


def test_rollback_restores_listed_files_and_removes_new_ones(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    write(root, "a.md", "one\n")
    write(root, "b.md", "keep\n")
    write(root, "other.md", "other\n")
    repo.stage(["a.md", "b.md", "other.md"])
    head = repo.commit("base")
    write(root, "a.md", "modified\n")  # tracked, modified, listed
    (root / "b.md").unlink()  # tracked, deleted, listed
    write(root, "other.md", "edited by hand\n")  # tracked, edited, NOT listed
    write(root, "notes/new.md")  # new and staged, listed
    write(root, "notes/new-unstaged.md")  # new, never staged, listed
    write(root, "draft.md", "draft\n")  # untracked draft, not listed
    write(root, "ignored.log", "log\n")
    raw_git(root, "config", "core.excludesFile", "/dev/null")
    (root / ".git" / "info" / "exclude").write_text("ignored.log\n")
    repo.stage(["a.md", "notes/new.md"])
    repo.discard_changes(created=["notes/new.md", "notes/new-unstaged.md"], modified=["a.md", "b.md"])
    assert repo.head() == head
    assert (root / "a.md").read_text() == "one\n"
    assert (root / "b.md").read_text() == "keep\n"
    assert not (root / "notes/new.md").exists()
    assert not (root / "notes/new-unstaged.md").exists()
    assert (root / "other.md").read_text() == "edited by hand\n"
    assert (root / "draft.md").read_text() == "draft\n"
    assert (root / "ignored.log").exists()
    assert raw_git(root, "diff", "--cached", "--name-only") == ""


def test_rollback_refuses_directories_and_the_whole_tree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    write(root, "notes/a.md")
    write(root, "draft.md", "draft\n")
    for bad in (".", "notes", ""):
        with pytest.raises(GitError):
            repo.discard_changes(created=[bad], modified=[])
    assert (root / "draft.md").exists()
    assert (root / "notes/a.md").exists()


def test_rollback_before_the_first_commit(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    write(root, "a.md")
    write(root, "draft.md")
    repo.stage(["a.md"])
    repo.discard_changes(created=["a.md"], modified=[])
    assert not (root / "a.md").exists()
    assert (root / "draft.md").exists()
    assert repo.head() is None
    assert raw_git(root, "diff", "--cached", "--name-only") == ""


def test_rollback_removes_a_planted_symlink_entry_without_following_it(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    victim = tmp_path / "victim.txt"
    victim.write_text("precious\n")
    write(root, "base.md")
    repo.stage(["base.md"])
    repo.commit("base")
    (root / "planted.md").symlink_to(victim)
    repo.discard_changes(created=["planted.md"], modified=[])
    assert not (root / "planted.md").is_symlink()
    assert victim.read_text() == "precious\n"


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
    assert len(scrub("x" * 5000)) == 200


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


def test_symlink_to_dot_git_is_rejected(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    (root / "lnk").symlink_to(root / ".git")
    for path in ("lnk/config", "lnk/hooks/pre-commit"):
        with pytest.raises(GitError):
            repo.stage([path])
        with pytest.raises(GitError):
            repo.discard_changes(created=[path], modified=[])


def test_symlinked_parent_inside_the_repository_is_rejected_for_writes(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    (root / "real").mkdir()
    (root / "alias").symlink_to(root / "real")
    write(root, "real/a.md")
    with pytest.raises(GitError):
        repo.stage(["alias/a.md"])


def test_errors_never_expose_paths_urls_or_credentials(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(tmp_path / "repo", "a.md")
    repo.stage(["a.md"])
    repo.commit("m")
    url = "https://u:p@ss@127.0.0.1:9/owner/content.git?access_token=tok3n"
    with pytest.raises(GitError) as caught:
        repo.push(url, "main", timeout=20)
    text = str(caught.value)
    for leaked in ("ss@", "tok3n", "access_token", "u:p", "127.0.0.1", "content.git", str(tmp_path)):
        assert leaked not in text
    with pytest.raises(GitError) as pathspec:
        repo._run(["add", "--", "secret-note-name.md"])
    assert "secret-note-name" not in str(pathspec.value)
    assert str(tmp_path) not in str(pathspec.value)


def test_scrub_handles_userinfo_with_at_sign_and_query() -> None:
    text = "fatal: unable to access 'https://u:p@ss@host.example/r.git?access_token=x': nope"
    cleaned = scrub(text)
    for leaked in ("ss@", "access_token", "host.example", "u:p"):
        assert leaked not in cleaned
    assert "nope" in scrub("fatal: nope")


@pytest.mark.parametrize("name", ["GIT_DIR", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_COUNT", "HOME", "PATH", "GIT_EXEC_PATH"])
def test_passthrough_env_is_an_allowlist(tmp_path: Path, name: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(GitError, match="not allowed"):
        GitRepo(root, identity=IDENTITY, passthrough_env=[name])
    GitRepo(root, identity=IDENTITY, passthrough_env=["SSH_AUTH_SOCK", "https_proxy"])


def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "forking-git"
    script.write_text(f"#!/bin/sh\nsleep 60 &\necho $! > '{pid_file}'\nsleep 60\n")
    script.chmod(0o755)
    repo = GitRepo(root, identity=IDENTITY, git_executable=str(script), timeout=1.0)
    with pytest.raises(GitError, match="timed out"):
        repo.head()
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail("a descendant of git survived the timeout")


def test_init_ignores_global_template_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    templates = tmp_path / "templates" / "hooks"
    templates.mkdir(parents=True)
    (templates / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    config = tmp_path / "global.gitconfig"
    config.write_text(f"[init]\n\ttemplateDir = {tmp_path / 'templates'}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    make_repo(tmp_path)
    assert not (tmp_path / "repo" / ".git" / "hooks" / "pre-commit").exists()


def test_a_symlinked_dot_git_is_refused_at_construction(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (root / ".git").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(GitError):
        GitRepo(root, identity=IDENTITY)


def test_push_by_configured_remote_name(tmp_path: Path) -> None:
    remote = bare_remote(tmp_path)
    repo = make_repo(tmp_path)
    root = tmp_path / "repo"
    raw_git(root, "remote", "add", "backup", str(remote))
    write(root, "a.md")
    repo.stage(["a.md"])
    head = repo.commit("m")
    GitVersionControl(repo, remote="backup", branch="main").push()
    assert raw_git(remote, "rev-parse", "main").strip() == head


def test_git_version_control_validates_its_arguments(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    with pytest.raises(GitError):
        GitVersionControl(repo, remote=None, branch="--delete")
    with pytest.raises(GitError):
        GitVersionControl(repo, remote="--upload-pack=x", branch="main")


def test_a_failed_push_to_a_missing_local_remote_is_an_error(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(tmp_path / "repo", "a.md")
    repo.stage(["a.md"])
    repo.commit("m")
    with pytest.raises(GitError):
        GitVersionControl(repo, remote=str(tmp_path / "nope.git"), branch="main").push()


def test_transports_other_than_https_ssh_file_are_denied(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    write(tmp_path / "repo", "a.md")
    repo.stage(["a.md"])
    repo.commit("m")
    marker = tmp_path / "ext-ran"
    with pytest.raises(GitError):
        repo.push(f"ext::sh -c touch% {marker}", "main")
    with pytest.raises(GitError):
        repo.push("git://127.0.0.1:9/x.git", "main", timeout=10)
    assert not marker.exists()
