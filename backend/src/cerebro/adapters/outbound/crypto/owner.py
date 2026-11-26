"""Protected local owner credentials. The fixed v1 format selects one scrypt parameter set."""

import fcntl
import hashlib
import hmac
import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from cerebro.application.errors import PasswordDurabilityError

_PREFIX = b"CEREBRO-SCRYPT-V1\x00"
_SIZE = len(_PREFIX) + 16 + 16 + 32  # generation, salt, derived key
_MAX_PASSWORD = 1024


def _derive(password: bytes, salt: bytes) -> bytes:
    return hashlib.scrypt(password, salt=salt, n=2**17, r=8, p=1, dklen=32, maxmem=192 * 1024**2)


def _check_owner(fd: int, *, directory: bool = False) -> None:
    info = os.fstat(fd)
    valid_kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not valid_kind or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise PermissionError("Owner credential storage requires private permissions and ownership")


@contextmanager
def _directory(path: Path) -> Iterator[int]:
    fd = os.open(path, os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY)
    try:
        _check_owner(fd, directory=True)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _hash_slot(directory: int, name: str, *, wait: bool) -> Iterator[None]:
    # This inode is permanent across verifier replacements and process restarts.
    flags = os.O_NOFOLLOW | os.O_CREAT | os.O_RDWR | os.O_NONBLOCK
    fd = os.open(name + ".lock", flags, mode=0o600, dir_fd=directory)
    try:
        _check_owner(fd)
        fcntl.flock(fd, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _snapshot(directory: int, name: str) -> tuple[str, bytes, bytes]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as handle:
        _check_owner(fd)
        data = handle.read(_SIZE + 1)
    if len(data) != _SIZE or not data.startswith(_PREFIX):
        raise ValueError("Invalid owner credential format")
    fields = data[len(_PREFIX) :]
    return fields[:16].hex(), fields[16:32], fields[32:]


def _replace(directory: int, name: str, data: bytes) -> None:
    temp = ".owner-" + secrets.token_hex(16)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode=0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, name, src_dir_fd=directory, dst_dir_fd=directory)
        try:
            os.fsync(directory)
        except OSError:
            raise PasswordDurabilityError() from None
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temp, dir_fd=directory)


class OwnerCredentials:
    """Share a verifier and one hash slot across processes using the same owner-only directory."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def provision(self, password: bytes) -> None:
        if not 1 <= len(password) <= _MAX_PASSWORD:
            raise ValueError("Invalid password size")
        with _directory(self._path.parent) as directory, _hash_slot(directory, self._path.name, wait=True):
            generation = secrets.token_bytes(16)
            salt = secrets.token_bytes(16)
            _replace(directory, self._path.name, _PREFIX + generation + salt + _derive(password, salt))

    def verify(self, password: bytes) -> str | None:
        if not 1 <= len(password) <= _MAX_PASSWORD:
            return None
        try:
            with _directory(self._path.parent) as directory, _hash_slot(directory, self._path.name, wait=False):
                generation, salt, expected = _snapshot(directory, self._path.name)
                return generation if hmac.compare_digest(_derive(password, salt), expected) else None
        except OSError, ValueError:
            return None

    def generation(self) -> str | None:
        try:
            with _directory(self._path.parent) as directory:
                generation, _, _ = _snapshot(directory, self._path.name)
                return generation
        except OSError, ValueError:
            return None
