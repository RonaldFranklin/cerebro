"""Create the owner's initial content repository before the container needs a vocabulary."""

import ctypes
import errno
import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory

from cerebro.adapters.outbound.filesystem import VocabularyError, load_vocabulary
from cerebro.adapters.outbound.git import GitError, GitIdentity, GitRepo
from cerebro.bootstrap.settings import Settings
from cerebro.domain.vocabulary import INITIAL_USER_NOTE_TYPES

_DIRECTORIES = ("core", "hubs", "notes", "schema")
_SCHEMA = ("schema/areas.yaml", "schema/types.yaml")


class InitializationError(Exception):
    """Content initialization refused or failed. Existing owner files are preserved."""


def _check_existing(root: Path, identity: GitIdentity) -> None:
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise InitializationError("content directory must be private and owned by the current user")
    if not all(stat.S_ISDIR((root / name).lstat().st_mode) for name in (".git", *_DIRECTORIES)):
        raise InitializationError("incomplete content repository")
    load_vocabulary(root)
    git = GitRepo(root, identity=identity)
    if not all(git.regular_file_in_head(path) for path in _SCHEMA):
        raise InitializationError("content repository has no schema commit")


def _publish(checkout: Path, root: Path) -> None:
    """Atomically publish on Linux/WSL without replacing even an empty directory created by a racing owner."""
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError:
        raise InitializationError("the filesystem requires atomic no-replace rename support") from None
    # Linux renameat2(AT_FDCWD, old, AT_FDCWD, new, RENAME_NOREPLACE).
    if rename(-100, os.fsencode(checkout), -100, os.fsencode(root), 1) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def initialize_content(settings: Settings) -> None:
    root = settings.content_repo
    identity = GitIdentity(name=settings.git_name, email=settings.git_email)
    try:
        if root.parent.resolve() != root.parent or root.is_symlink():
            raise InitializationError("content path must not traverse symlinks")
        if any(path.resolve().is_relative_to(root) for path in (settings.key_file, settings.state_dir)):
            raise InitializationError("keep secrets and state outside the content repository")
        if root.exists():
            _check_existing(root, identity)
            return
        root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Build beside the target: a failed git commit or schema write never publishes a partial repository.
        with TemporaryDirectory(prefix=".cerebro-init-", dir=root.parent) as staging:
            checkout = Path(staging) / "content"
            checkout.mkdir(mode=0o700)
            git = GitRepo(checkout, identity=identity)
            git.init(settings.git_branch)
            for name in _DIRECTORIES:
                (checkout / name).mkdir(mode=0o700)
            (checkout / _SCHEMA[0]).write_text("areas: []\n", encoding="utf-8")
            names = sorted(item.value for item in INITIAL_USER_NOTE_TYPES)
            (checkout / _SCHEMA[1]).write_text(
                "types:\n" + "".join(f"  - {name}\n" for name in names), encoding="utf-8"
            )
            git.stage(_SCHEMA)
            git.commit("Initialize content schema", paths=_SCHEMA)
            try:
                _publish(checkout, root)
            except OSError as error:
                if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                    raise
                _check_existing(root, identity)
    except OSError, GitError, VocabularyError, ValueError, RuntimeError:
        raise InitializationError("cannot initialize or verify the content repository") from None
