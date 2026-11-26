"""In-memory owner verifier for application tests."""

import secrets


class FakeOwnerCredentials:
    def __init__(self) -> None:
        self._password: bytes | None = None
        self._generation: str | None = None

    def provision(self, password: bytes) -> None:
        if not 1 <= len(password) <= 1024:
            raise ValueError("Invalid password size")
        self._password = password
        self._generation = secrets.token_hex(16)

    def verify(self, password: bytes) -> str | None:
        return self._generation if self._password == password else None

    def generation(self) -> str | None:
        return self._generation
