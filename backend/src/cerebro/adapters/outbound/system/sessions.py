"""The service process owns one session table shared by its listeners."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from cerebro.application.browser import StoredSession


class MemorySessions:
    def __init__(self) -> None:
        self._lock = Lock()
        self._rows: dict[bytes, StoredSession] = {}

    @contextmanager
    def locked(self) -> Iterator[dict[bytes, StoredSession]]:
        with self._lock:
            yield self._rows
