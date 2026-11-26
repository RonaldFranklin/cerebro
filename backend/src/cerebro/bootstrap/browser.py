"""Provision the owner verifier outside the content and application repositories."""

import os
from pathlib import Path

from cerebro.adapters.outbound.crypto.owner import OwnerCredentials
from cerebro.bootstrap.settings import ConfigurationError, Settings

_APPLICATION_MARKER = Path("backend/src/cerebro/bootstrap/browser.py")


def _application_checkout(path: Path) -> bool:
    # A source file reached through a link that leaves this directory does not make it a checkout.
    marker = path / _APPLICATION_MARKER
    return marker.is_file() and marker.resolve().is_relative_to(path)


def private_runtime_directory(path: Path, content_repo: Path, name: str) -> Path:
    directory = path.resolve()
    if directory.is_relative_to(content_repo.resolve()) or any(
        _application_checkout(ancestor) for ancestor in (directory, *directory.parents)
    ):
        raise ConfigurationError(f"the {name} directory must be outside the content and application repositories")
    if path.is_symlink():
        raise ConfigurationError(f"a regular {name} directory is required")
    return directory


def owner_credentials(settings: Settings) -> OwnerCredentials:
    directory = private_runtime_directory(settings.state_dir, settings.content_repo, "state")
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
