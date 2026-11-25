"""Loading Fernet keys from a key file.

File format: one urlsafe-base64 Fernet key per line; blank lines and lines starting with ``#`` are ignored. The
first key encrypts, all keys decrypt (so a rotation prepends a new key and keeps the old ones until every
restricted note is re-encrypted).

Safety rules (a key file is a secret, ADR-0008):

- the path must be a regular file, not a symlink (opened with ``O_NOFOLLOW`` and checked on the descriptor);
- the file must have no group or other permission bits (anything more open than ``0600`` is refused);
- its parent directory must have no group or other permission bits (anything more open than ``0700``); ownership
  is not checked, so a mounted secrets directory works as long as it is closed to others.

Errors name the path and the problem, never the contents.
"""

import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet

from cerebro.adapters.outbound.crypto.errors import KeyFileError

MAX_KEY_FILE_BYTES = 64 * 1024


def load_keys(path: Path | str) -> tuple[bytes, ...]:
    """Read and validate the keys of ``path``; the first one is the encrypting key."""
    file_path = Path(os.path.abspath(path))
    _check_directory(file_path)
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
        key = stripped.encode("utf-8")
        try:
            Fernet(key)
        except ValueError, TypeError:
            raise KeyFileError(f"key file {file_path}: line {number} is not a valid Fernet key") from None
        keys.append(key)
    if not keys:
        raise KeyFileError(f"key file {file_path} contains no keys")
    return tuple(keys)


def _check_directory(file_path: Path) -> None:
    try:
        mode = file_path.parent.stat().st_mode
    except OSError:
        raise KeyFileError(f"cannot inspect the directory of key file {file_path}") from None
    if not stat.S_ISDIR(mode):
        raise KeyFileError(f"the parent of key file {file_path} is not a directory")
    if mode & 0o077:
        raise KeyFileError(f"the directory of key file {file_path} is more open than 0700")


def _read_safely(file_path: Path) -> bytes:
    try:
        fd = os.open(file_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise KeyFileError(f"cannot open key file {file_path} (missing, unreadable or a symlink)") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise KeyFileError(f"key file {file_path} is not a regular file")
        if info.st_mode & 0o077:
            raise KeyFileError(f"key file {file_path} is more open than 0600")
        if info.st_size > MAX_KEY_FILE_BYTES:
            raise KeyFileError(f"key file {file_path} is larger than {MAX_KEY_FILE_BYTES} bytes")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read(MAX_KEY_FILE_BYTES + 1)
    finally:
        os.close(fd)
