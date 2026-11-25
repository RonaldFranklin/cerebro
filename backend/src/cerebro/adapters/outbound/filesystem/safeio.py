"""File operations that refuse symlinks and stay inside the content root.

Paths are ``/``-separated and relative to ``root``; callers build them only from fixed folder names and validated
``NoteId`` slugs. Every directory component is checked with ``lstat`` (a symlink or a non-directory is refused) and
files are opened with ``O_NOFOLLOW``. A file that is not a regular file, or is larger than the cap, is refused
before it is read.
"""

import contextlib
import errno
import os
import secrets
import stat
from pathlib import Path

_TEMP_ATTEMPTS = 8


def _refuse(message: str) -> OSError:
    return OSError(errno.ELOOP, message)


def check_directories(root: Path, parts: tuple[str, ...], *, create: bool = False) -> Path:
    """The directory ``root/parts...`` with no symlink on the way. Missing directories raise
    ``FileNotFoundError``, or are created (mode 0755 before umask) when ``create`` is true."""
    current = root
    for part in parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not create:
                raise
            current.mkdir(mode=0o755)
            continue
        if stat.S_ISLNK(info.st_mode):
            raise _refuse("symlink in path")
        if not stat.S_ISDIR(info.st_mode):
            raise _refuse("not a directory")
    return current


def read_regular_file(root: Path, relative: str, max_bytes: int) -> bytes:
    """The bytes of a regular file of at most ``max_bytes``. ``FileNotFoundError`` if it does not exist;
    ``OSError`` for a symlink, a non-regular file or one that is too large."""
    *folders, name = relative.split("/")
    directory = check_directories(root, tuple(folders))
    fd = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _refuse("not a regular file")
        if info.st_size > max_bytes:
            raise OSError(errno.EFBIG, "file too large")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > max_bytes:
        raise OSError(errno.EFBIG, "file too large")
    return data


def write_file_atomic(root: Path, relative: str, data: bytes, *, mode: int) -> None:
    """Replace (or create) ``root/relative`` so that readers see the old or the new content, never a mix.

    The data goes to a hidden temp file in the same directory (``O_EXCL``, ``mode``), is fsynced, and is renamed
    over the target; the directory is fsynced afterwards. A symlink at the target is refused. The temp file is
    removed on any failure."""
    *folders, name = relative.split("/")
    directory = check_directories(root, tuple(folders), create=True)
    target = directory / name
    with contextlib.suppress(FileNotFoundError):
        if stat.S_ISLNK(target.lstat().st_mode):
            raise _refuse("target is a symlink")
    for _ in range(_TEMP_ATTEMPTS):
        temp = directory / f".{name}.{secrets.token_hex(8)}.tmp"
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
        except FileExistsError:
            continue
        break
    else:  # pragma: no cover - 64 random bits colliding eight times in a row
        raise OSError(errno.EEXIST, "no free temp file name")
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise
    fsync_directory(directory)


def fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
