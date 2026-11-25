"""Advisory file lock for the content repository (``fcntl.flock``).

The lock file lives inside ``.git/`` so it is never part of the tracked tree. Every call opens its own file
description, so the lock also serialises threads of one process, not just processes. Acquisition never blocks
forever: it polls without blocking until ``timeout`` and then raises ``WriterBusyError``.
"""

import fcntl
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cerebro.adapters.outbound.filesystem.errors import NoteWriteError, WriterBusyError

POLL_INTERVAL = 0.02


@contextmanager
def file_lock(path: Path, *, exclusive: bool, timeout: float, label: str = "content repository lock") -> Iterator[None]:
    """``label`` names the lock in error messages (a fixed text chosen by the caller, never user data)."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    except OSError:
        raise NoteWriteError(f"could not open the {label} file") from None
    try:
        flags = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, flags)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise WriterBusyError(f"the {label} is held by another writer") from None
                time.sleep(POLL_INTERVAL)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
