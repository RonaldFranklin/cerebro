"""Owner browser authentication storage boundaries."""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from cerebro.application.browser import StoredSession


class Credentials(Protocol):
    def provision(self, password: bytes) -> None: ...
    def verify(self, password: bytes) -> str | None:
        """Return the generation of the verified snapshot."""
        ...

    def generation(self) -> str | None:
        """Read the current generation, including changes outside the service."""
        ...


class LoginAttempts(Protocol):
    def reserve(self, now: datetime) -> int:
        """Atomically reserve an attempt or return the positive retry delay in seconds."""
        ...


class SessionStore(Protocol):
    def locked(self) -> AbstractContextManager[dict[bytes, StoredSession]]:
        """Exclusive access to this process's ephemeral sessions, keyed by digest."""
        ...
