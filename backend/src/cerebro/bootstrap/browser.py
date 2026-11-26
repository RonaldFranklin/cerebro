"""Provision the owner verifier outside the content and application repositories."""

import os
from pathlib import Path

from cerebro.adapters.outbound.crypto.owner import OwnerCredentials
from cerebro.bootstrap.settings import ConfigurationError, Settings

_SOURCE_MODULE = Path("backend/src/cerebro/bootstrap/browser.py")


def _repository_directory(path: Path, installed_module: Path) -> bool:
    # Explicit --work-tree checkouts can have no Git marker. Recognize the running source
    # checkout and the standard application source layout when running an installed wheel.
    source = path / _SOURCE_MODULE
    if source == installed_module or (
        (path / "backend/pyproject.toml").is_file()
        and source.is_file()
        and source.parent.resolve().is_relative_to(path / "backend")
    ):
        return True
    # Ordinary and linked worktrees have a .git directory or gitfile. Inspect the destination's
    # ancestors as well, since separate and bare Git directories can live outside every checkout.
    # A common Git directory owns HEAD and objects; linked-worktree metadata owns HEAD and commondir.
    if (path / ".git").exists():
        return True
    head = path / "HEAD"
    return (head.is_file() or head.is_symlink()) and ((path / "objects").is_dir() or (path / "commondir").is_file())


def owner_credentials(settings: Settings) -> OwnerCredentials:
    directory = settings.state_dir.resolve()
    installed_module = Path(__file__).resolve()
    excluded = [settings.content_repo.resolve()]
    if explicit_worktree := os.environ.get("GIT_WORK_TREE"):
        excluded.append(Path(explicit_worktree).resolve())
    if any(directory.is_relative_to(root) for root in excluded) or any(
        _repository_directory(ancestor, installed_module) for ancestor in (directory, *directory.parents)
    ):
        raise ConfigurationError("owner credentials require a state directory outside repositories")
    if settings.state_dir.is_symlink():
        raise ConfigurationError("owner credentials require a regular state directory")
    verifier = directory / "owner.verifier"
    if not verifier.is_file():
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # A previous or concurrent mkdir can be visible before its parent entry is durable.
        # Flush the chain even on retry. A directory owned by another uid predates our mkdirs.
        child = directory
        while child.parent != child and child.stat().st_uid == os.geteuid():
            parent = child.parent
            fd = os.open(parent, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            child = parent
    return OwnerCredentials(verifier)
