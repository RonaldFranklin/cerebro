"""Loading Fernet keys from a key file.

File format: one urlsafe-base64 Fernet key per line; blank lines and lines starting with ``#`` are ignored. The
first key encrypts, all keys decrypt (so a rotation prepends a new key and keeps the old ones until every
restricted note is re-encrypted).

Safety rules (a key file is a secret, ADR-0008):

- the path must be a regular file, not a symlink (opened with ``O_NOFOLLOW`` and checked on the descriptor);
- the file must have no group or other permission bits (anything more open than ``0600`` is refused);
- its parent directory must have no group or other permission bits (anything more open than ``0700``);
- the file and its parent directory must be owned by the effective user or by root (trade-off: root-owned
  files are accepted so a root-provisioned secrets directory works, at the cost of trusting root, which can read
  everything anyway);
- every key line must be strictly urlsafe base64 (``[A-Za-z0-9_-]`` plus ``=`` padding) before it is handed to
  Fernet, so junk characters are refused instead of silently dropped by the base64 decoder.

The directory is opened first and the file is opened relative to that descriptor (``dir_fd``), with every check
made on descriptors, so the path cannot be swapped between check and use.

By design, Docker/Kubernetes secret mounts (typically ``0755`` directories, ``0444`` files and symlinks) are
refused. Copy the key at start-up into a ``0700`` directory as a ``0600`` file owned by the service user.

Errors name the path and the problem, never the contents.
"""

import os
import re
import stat
from pathlib import Path

from cryptography.fernet import Fernet

from cerebro.adapters.outbound.crypto.errors import KeyFileError

MAX_KEY_FILE_BYTES = 64 * 1024


_KEY_LINE = re.compile(r"[A-Za-z0-9_-]{43}=", re.ASCII)  # a 32-byte key: 43 characters and one padding


def load_keys(path: Path | str) -> tuple[bytes, ...]:
    """Read and validate the keys of ``path``; the first one is the encrypting key."""
    file_path = Path(os.path.abspath(path))
    raw = _read_safely(file_path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise KeyFileError(f"key file {file_path} is not valid UTF-8 text") from None
    keys: list[bytes] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        invalid = KeyFileError(f"key file {file_path}: line {number} is not a valid Fernet key")
        if _KEY_LINE.fullmatch(stripped) is None:
            raise invalid
        key = stripped.encode("ascii")
        try:
            Fernet(key)
        except ValueError, TypeError:
            raise invalid from None
        keys.append(key)
    if not keys:
        raise KeyFileError(f"key file {file_path} contains no keys")
    return tuple(keys)


def _owner_ok(uid: int) -> bool:
    return uid in (os.geteuid(), 0)


def _read_safely(file_path: Path) -> bytes:
    try:
        dir_fd = os.open(file_path.parent, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        raise KeyFileError(f"cannot open the directory of key file {file_path}") from None
    try:
        dir_info = os.fstat(dir_fd)
        if dir_info.st_mode & 0o077:
            raise KeyFileError(f"the directory of key file {file_path} is more open than 0700")
        if not _owner_ok(dir_info.st_uid):
            raise KeyFileError(f"the directory of key file {file_path} is not owned by the current user or root")
        try:
            fd = os.open(file_path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        except OSError:
            raise KeyFileError(f"cannot open key file {file_path} (missing, unreadable or a symlink)") from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise KeyFileError(f"key file {file_path} is not a regular file")
            if info.st_mode & 0o077:
                raise KeyFileError(f"key file {file_path} is more open than 0600")
            if not _owner_ok(info.st_uid):
                raise KeyFileError(f"key file {file_path} is not owned by the current user or root")
            if info.st_size > MAX_KEY_FILE_BYTES:
                raise KeyFileError(f"key file {file_path} is larger than {MAX_KEY_FILE_BYTES} bytes")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                data = handle.read(MAX_KEY_FILE_BYTES + 1)
            if len(data) > MAX_KEY_FILE_BYTES:
                raise KeyFileError(f"key file {file_path} is larger than {MAX_KEY_FILE_BYTES} bytes")
            return data
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)
