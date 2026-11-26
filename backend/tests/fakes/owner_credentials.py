"""In-memory owner verifier for application tests."""

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock


class FakeOwnerCredentials:
    def __init__(self) -> None:
        self._lock = Lock()
        self._password: bytes | None = None
        self._generation: str | None = None

    def provision(self, password: bytes) -> None:
        if not 1 <= len(password) <= 1024:
            raise ValueError("Invalid password size")
        with self._lock:
            self._password = password
            self._generation = secrets.token_hex(16)

    def verify(self, password: bytes) -> str | None:
        with self._lock:
            return self._generation if self._password == password else None

    def generation(self) -> str | None:
        return self._generation

    @contextmanager
    def issuing(self, generation: str) -> Iterator[bool]:
        with self._lock:
            yield self._generation == generation
