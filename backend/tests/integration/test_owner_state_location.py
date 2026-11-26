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
