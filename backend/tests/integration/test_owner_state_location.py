"""The owner verifier lives outside the content repository and the application repository."""

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


@pytest.mark.parametrize("state_path", ["content/state", "content/.git/private/state", "content"])
def test_refuses_state_inside_the_content_repository(tmp_path: Path, state_path: str) -> None:
    content = tmp_path / "content"
    _git(tmp_path, "init", str(content))
    state = tmp_path / state_path
    before = sorted(content.rglob("*"))
    with pytest.raises(ConfigurationError, match="outside the content and application repositories"):
        owner_credentials(_settings(content, state))
    assert sorted(content.rglob("*")) == before


def test_refuses_a_link_that_leads_into_the_content_repository(tmp_path: Path) -> None:
    content = tmp_path / "content"
    content.mkdir()
    (tmp_path / "alias").symlink_to(content, target_is_directory=True)
    with pytest.raises(ConfigurationError, match="outside the content and application repositories"):
        owner_credentials(_settings(content, tmp_path / "alias" / "state"))
    assert not (content / "state").exists()


def test_refuses_a_state_directory_that_is_a_link(tmp_path: Path) -> None:
    (tmp_path / "target").mkdir(mode=0o700)
    (tmp_path / "state").symlink_to(tmp_path / "target", target_is_directory=True)
    with pytest.raises(ConfigurationError, match="regular state directory"):
        owner_credentials(_settings(tmp_path / "content", tmp_path / "state"))


def test_refuses_state_inside_the_running_application_checkout() -> None:
    state = Path(__file__).resolve().parents[3] / "never-created" / "state"
    with pytest.raises(ConfigurationError, match="outside the content and application repositories"):
        owner_credentials(_settings(state.parents[2] / "unused-content", state))
    assert not state.parent.exists()


@pytest.mark.parametrize("layout", ["plain", "linked-module", "shared-src"])
def test_refuses_state_inside_another_application_checkout(tmp_path: Path, layout: str) -> None:
    checkout = tmp_path / "application"
    module = checkout / "backend/src/cerebro/bootstrap/browser.py"
    if layout == "shared-src":
        (checkout / "shared/src").mkdir(parents=True)
        (checkout / "backend").mkdir()
        (checkout / "backend/src").symlink_to("../shared/src", target_is_directory=True)
    module.parent.mkdir(parents=True)
    if layout == "linked-module":
        module.with_name("implementation.py").touch()
        module.symlink_to("implementation.py")
    else:
        module.touch()
    state = checkout / "new" / "state"
    with pytest.raises(ConfigurationError, match="outside the content and application repositories"):
        owner_credentials(_settings(tmp_path / "content", state))
    assert not state.parent.exists()


def test_accepts_a_state_directory_under_an_unrelated_repository(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _git(tmp_path, "init", str(home))  # e.g. a home directory that versions its dotfiles
    state = home / ".local/state/cerebro"
    owner_credentials(_settings(tmp_path / "content", state))
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


def test_external_source_directory_does_not_mark_backend_as_a_checkout(tmp_path: Path) -> None:
    external = tmp_path / "application/backend/src"
    module = external / "cerebro/bootstrap/browser.py"
    module.parent.mkdir(parents=True)
    module.touch()
    unrelated = tmp_path / "other-project"
    backend = unrelated / "backend"
    backend.mkdir(parents=True)
    (backend / "pyproject.toml").touch()
    (backend / "src").symlink_to(external, target_is_directory=True)
    state = unrelated / "state"
    owner_credentials(_settings(tmp_path / "content", state))
    assert state.is_dir()
