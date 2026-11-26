"""Owner verifier location across Git's worktree, bare and separate metadata layouts."""

from pathlib import Path
from subprocess import run

import pytest

from cerebro.bootstrap.browser import owner_credentials
from cerebro.bootstrap.settings import ConfigurationError, Settings


def _settings(content: Path, state: Path) -> Settings:
    return Settings(
        content_repo=content, state_dir=state, key_file=state.parent / "key", git_name="Test", git_email="x"
    )


def _git(root: Path, *arguments: str) -> None:
    run(["git", "-C", str(root), *arguments], check=True, capture_output=True)


@pytest.mark.parametrize("metadata", ["separate", "common", "linked", "bare"])
@pytest.mark.parametrize("content_checkout", [True, False], ids=["content", "application"])
def test_refuses_git_metadata_before_creating_state(tmp_path: Path, metadata: str, content_checkout: bool) -> None:
    checkout = tmp_path / "checkout"
    git_dir = tmp_path / "metadata"
    if metadata == "bare":
        _git(tmp_path, "init", "--bare", str(git_dir))
        checkout = git_dir
    else:
        _git(tmp_path, "init", "--separate-git-dir", str(git_dir), str(checkout))
        if metadata in {"common", "linked"}:
            _git(
                checkout,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "Fixture",
            )
            linked = tmp_path / "linked"
            _git(checkout, "worktree", "add", "--detach", str(linked))
            checkout = linked
            if metadata == "linked":
                git_dir = git_dir / "worktrees" / "linked"
                assert (git_dir / "commondir").is_file()

    state = git_dir / "private" / "state"
    content = checkout if content_checkout else tmp_path / "independent-content"
    with pytest.raises(ConfigurationError, match="outside repositories"):
        owner_credentials(_settings(content, state))
    assert not state.parent.exists()


def test_accepts_private_sibling_of_git_metadata(tmp_path: Path) -> None:
    content = tmp_path / "checkout"
    _git(tmp_path, "init", "--separate-git-dir", str(tmp_path / "metadata"), str(content))
    state = tmp_path / "private"
    owner_credentials(_settings(content, state))
    assert state.is_dir()


def test_first_verifier_flushes_directory_entries_during_concurrent_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    directory = tmp_path / "nested" / "state"
    settings = _settings(tmp_path / "content", directory)
    first_sync = Event()
    release = Event()
    flushed: set[int] = set()
    parent_inode = tmp_path.stat().st_ino
    original = os.fsync

    def fail_first_sync(fd: int) -> None:
        inode = os.fstat(fd).st_ino
        if inode == parent_inode and not first_sync.is_set():
            first_sync.set()
            assert release.wait(timeout=5)
            raise OSError("simulated parent sync failure")
        original(fd)
        flushed.add(inode)

    monkeypatch.setattr(os, "fsync", fail_first_sync)
    with ThreadPoolExecutor(max_workers=1) as worker:
        interrupted = worker.submit(owner_credentials, settings)
        try:
            assert first_sync.wait(timeout=5)
            verifier = owner_credentials(settings)
            assert {parent_inode, directory.parent.stat().st_ino} <= flushed
            verifier.provision(b"fixture")
        finally:
            release.set()
        with pytest.raises(OSError, match="simulated parent sync failure"):
            interrupted.result(timeout=5)
    assert verifier.verify(b"fixture") == verifier.generation()


def test_parent_sync_failure_prevents_first_provision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    directory = tmp_path / "nested" / "state"
    settings = _settings(tmp_path / "content", directory)

    def fail(fd: int) -> None:
        raise OSError("directory sync failed")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail)
        with pytest.raises(OSError, match="directory sync failed"):
            owner_credentials(settings).provision(b"fixture")
    assert directory.is_dir()
    assert not (directory / "owner.verifier").exists()
    verifier = owner_credentials(settings)
    verifier.provision(b"fixture")
    assert verifier.verify(b"fixture") is not None


@pytest.mark.parametrize("installation", ["source", "wheel", "wheel-linked-module"])
def test_refuses_explicit_application_worktree_without_gitfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installation: str
) -> None:
    from cerebro.bootstrap import browser

    checkout = tmp_path / "application"
    checkout.mkdir()
    git_dir = tmp_path / "metadata"
    _git(tmp_path, "init", "--bare", str(git_dir))
    module = checkout / "backend/src/cerebro/bootstrap/browser.py"
    module.parent.mkdir(parents=True)
    if installation == "wheel-linked-module":
        original = module.with_name("implementation.py")
        original.write_text("# Fixture application source\n")
        module.symlink_to(original.name)
    else:
        module.write_text("# Fixture application source\n")
    if installation != "source":
        (checkout / "backend/pyproject.toml").write_text('[project]\nname="cerebro"\n')
    explicit = ["--git-dir", str(git_dir), "--work-tree", str(checkout)]
    _git(tmp_path, *explicit, "add", "--", str(checkout / "backend"))
    _git(tmp_path, *explicit, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "Test")
    assert not (checkout / ".git").exists()
    installed = module if installation == "source" else tmp_path / "venv/site-packages/cerebro/bootstrap/browser.py"
    monkeypatch.setattr(browser, "__file__", str(installed))
    state = checkout / "new" / "state"
    with pytest.raises(ConfigurationError, match="outside repositories"):
        owner_credentials(_settings(tmp_path / "content", state))
    assert not state.parent.exists()


def test_declared_sparse_checkout_is_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    checkout = tmp_path / "sparse"
    checkout.mkdir()
    git_dir = tmp_path / "metadata"
    _git(tmp_path, "init", "--bare", str(git_dir))
    (checkout / "README.md").write_text("Fixture sparse tree\n")
    explicit = ["--git-dir", str(git_dir), "--work-tree", str(checkout)]
    _git(tmp_path, *explicit, "add", "--", str(checkout / "README.md"))
    _git(tmp_path, *explicit, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "Test")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_WORK_TREE", "sparse")
    with pytest.raises(ConfigurationError, match="outside repositories"):
        owner_credentials(_settings(tmp_path / "content", checkout / "state"))
    assert not (checkout / "state").exists()


def test_backend_symlink_does_not_mark_its_parent_as_a_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "application"
    source = checkout / "backend/src/cerebro/bootstrap/browser.py"
    source.parent.mkdir(parents=True)
    source.touch()
    (checkout / "backend/pyproject.toml").touch()
    home = tmp_path / "home"
    home.mkdir()
    (home / "backend").symlink_to(checkout / "backend", target_is_directory=True)
    state = home / ".local/state"
    owner_credentials(_settings(tmp_path / "content", state))
    assert state.is_dir()
