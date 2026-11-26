"""Provision the owner verifier outside the content and application repositories."""

from cerebro.adapters.outbound.crypto.owner import OwnerCredentials
from cerebro.bootstrap.settings import ConfigurationError, Settings


def owner_credentials(settings: Settings) -> OwnerCredentials:
    directory = settings.state_dir.resolve()
    if directory.is_relative_to(settings.content_repo.resolve()) or any(
        (ancestor / ".git").exists() for ancestor in (directory, *directory.parents)
    ):
        raise ConfigurationError("owner credentials require a state directory outside repositories")
    if settings.state_dir.is_symlink():
        raise ConfigurationError("owner credentials require a regular state directory")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return OwnerCredentials(directory / "owner.verifier")
