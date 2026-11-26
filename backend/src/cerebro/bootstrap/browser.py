"""Provision the owner verifier outside the content and application repositories."""

from pathlib import Path

from cerebro.adapters.outbound.crypto.owner import OwnerCredentials
from cerebro.bootstrap.settings import ConfigurationError, Settings


def _repository_directory(path: Path) -> bool:
    # Ordinary and linked worktrees have a .git directory or gitfile. Inspect the destination's
    # ancestors as well, since separate and bare Git directories can live outside every checkout.
    # A common Git directory owns HEAD and objects; linked-worktree metadata owns HEAD and commondir.
    if (path / ".git").exists():
        return True
    head = path / "HEAD"
    return (head.is_file() or head.is_symlink()) and ((path / "objects").is_dir() or (path / "commondir").is_file())


def owner_credentials(settings: Settings) -> OwnerCredentials:
    directory = settings.state_dir.resolve()
    if directory.is_relative_to(settings.content_repo.resolve()) or any(
        _repository_directory(ancestor) for ancestor in (directory, *directory.parents)
    ):
        raise ConfigurationError("owner credentials require a state directory outside repositories")
    if settings.state_dir.is_symlink():
        raise ConfigurationError("owner credentials require a regular state directory")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return OwnerCredentials(directory / "owner.verifier")
