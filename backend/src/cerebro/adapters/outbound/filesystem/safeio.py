"""File operations that refuse symlinks and stay inside the content root.

Paths are ``/``-separated and relative to ``root``; callers build them only from fixed folder names and validated
``NoteId`` slugs. Every directory is reached by walking from ``root`` with ``openat``-style calls
(``os.open(..., dir_fd=...)`` with ``O_DIRECTORY | O_NOFOLLOW`` per component), and the final file is opened,
created and renamed relative to the directory descriptor, so swapping a directory for a symlink between a check and
the use cannot redirect an operation. A file that is not a regular file, or is larger than the cap, is refused
before it is read.
"""

import contextlib
import errno
import os
import re
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_TEMP_ATTEMPTS = 8
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


@contextmanager
def open_directory(root: Path, parts: tuple[str, ...], *, create: bool = False) -> Iterator[int]:
    """A directory file descriptor for ``root/parts...`` with no symlink on the way (``OSError`` otherwise;
    ``FileNotFoundError`` for a missing directory unless ``create``, which makes it with mode 0755 before umask)."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in parts:
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o755, dir_fd=fd)
                child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_regular_file(root: Path, relative: str, max_bytes: int) -> bytes:
    """The bytes of a regular file of at most ``max_bytes``. ``FileNotFoundError`` if it does not exist;
    ``OSError`` for a symlink, a non-regular file or one that is too large."""
    *folders, name = relative.split("/")
    with open_directory(root, tuple(folders)) as directory:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.ELOOP, "not a regular file")
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
    with open_directory(root, tuple(folders), create=True) as directory:
        with contextlib.suppress(FileNotFoundError):
            if stat.S_ISLNK(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode):
                raise OSError(errno.ELOOP, "target is a symlink")
        for _ in range(_TEMP_ATTEMPTS):
            temp = f".{name}.{secrets.token_hex(8)}.tmp"
            try:
                fd = os.open(
                    temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=directory
                )
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
            os.replace(temp, name, src_dir_fd=directory, dst_dir_fd=directory)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp, dir_fd=directory)
            raise
        os.fsync(directory)


def set_mode(root: Path, relative: str, mode: int) -> None:
    """``chmod`` a regular file (never following a symlink)."""
    *folders, name = relative.split("/")
    with open_directory(root, tuple(folders)) as directory:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
    try:
        os.fchmod(fd, mode)
    finally:
        os.close(fd)


def remove_stale_temp_files(root: Path, folder: str, *, older_than: float, now: float, pattern: str) -> None:
    """Delete our leftover temp files (names fully matching the regex ``pattern``) of ``root/folder`` whose
    modification time is more than ``older_than`` seconds before ``now``. Best effort: errors are ignored."""
    matcher = re.compile(pattern)
    with contextlib.suppress(OSError), open_directory(root, (folder,)) as directory:
        for name in os.listdir(directory):
            if matcher.fullmatch(name) is None:
                continue
            with contextlib.suppress(OSError):
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and now - info.st_mtime > older_than:
                    os.unlink(name, dir_fd=directory)


def remove_file(root: Path, relative: str) -> None:
    """Delete a file (never following a symlink at the end); a missing file is fine."""
    *folders, name = relative.split("/")
    try:
        with open_directory(root, tuple(folders)) as directory:
            os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        return
