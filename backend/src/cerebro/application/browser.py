"""Owner login and session lifecycle, shared by both browser listeners."""

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from cerebro.application.errors import ApplicationError
from cerebro.application.ports.browser import Credentials, LoginAttempts, SessionStore
from cerebro.application.ports.system import Clock, IdGenerator

MAX_SESSIONS = 64
IDLE_LIFETIME = timedelta(minutes=30)
ABSOLUTE_LIFETIME = timedelta(hours=8)


class AuthenticationFailed(ApplicationError):  # noqa: N818
    def __init__(self, retry_after: int = 0) -> None:
        super().__init__("authentication failed")
        self.retry_after = retry_after


class CsrfRejected(AuthenticationFailed):
    """An authenticated session received an invalid synchronizer token."""


@dataclass(frozen=True, slots=True)
class BrowserBinding:
    listener: str
    origin: str


@dataclass(frozen=True, slots=True, repr=False)
class SessionInfo:
    csrf_token: str
    absolute_remaining_seconds: int


@dataclass(frozen=True, slots=True, repr=False)
class IssuedSession(SessionInfo):
    session_id: str


@dataclass(slots=True)
class StoredSession:
    binding: BrowserBinding
    generation: str
    created: datetime
    last_used: datetime
    csrf_token: str = field(repr=False)

    def expired(self, now: datetime) -> bool:
        return now < self.last_used or now - self.last_used >= IDLE_LIFETIME or now - self.created >= ABSOLUTE_LIFETIME


def _digest(secret: str) -> bytes:
    if not 32 <= len(secret) <= 128 or not secret.isascii():
        raise AuthenticationFailed()
    return hashlib.sha256(secret.encode("ascii")).digest()


class ProvisionOwnerPassword:
    """Local CLI only. A successful replacement starts a new credential generation."""

    def __init__(self, credentials: Credentials) -> None:
        self._credentials = credentials

    def execute(self, password: bytes) -> None:
        if not 1 <= len(password) <= 1024:
            raise ValueError("Invalid password size")
        self._credentials.provision(password)


class BrowserAuth:
    def __init__(
        self, credentials: Credentials, attempts: LoginAttempts, sessions: SessionStore, clock: Clock, ids: IdGenerator
    ) -> None:
        self._credentials = credentials
        self._attempts = attempts
        self._sessions = sessions
        self._clock = clock
        self._ids = ids

    def login(self, binding: BrowserBinding, password: bytes, previous: str | None = None) -> IssuedSession:
        retry = self._attempts.reserve(self._clock)
        if retry or not 1 <= len(password) <= 1024:
            raise AuthenticationFailed(retry)
        generation = self._credentials.verify(password)
        if generation is None:
            raise AuthenticationFailed()
        with self._credentials.issuing(generation) as current, self._sessions.locked() as rows:
            if not current:
                raise AuthenticationFailed()
            now = self._clock.now()
            for key, session in tuple(rows.items()):
                if session.generation != generation or session.expired(now):
                    del rows[key]
            if previous is not None:
                try:
                    old = _digest(previous)
                except AuthenticationFailed:
                    pass
                else:
                    if old in rows and rows[old].binding == binding:
                        del rows[old]
            if len(rows) >= MAX_SESSIONS:
                del rows[min(rows, key=lambda key: rows[key].last_used)]
            new = IssuedSession(
                session_id=self._ids.new_token_secret(),
                csrf_token=self._ids.new_token_secret(),
                absolute_remaining_seconds=int(ABSOLUTE_LIFETIME.total_seconds()),
            )
            rows[_digest(new.session_id)] = StoredSession(binding, generation, now, now, new.csrf_token)
            return new

    def session(self, binding: BrowserBinding, session_id: str, *, csrf: str | None = None) -> SessionInfo:
        with self._sessions.locked() as rows:
            row = self._authenticated(rows, binding, session_id, csrf)
            remaining = ABSOLUTE_LIFETIME - (row.last_used - row.created)
            return SessionInfo(row.csrf_token, int(remaining.total_seconds()))

    def check(self, binding: BrowserBinding, session_id: str) -> SessionInfo:
        """Authenticate without renewing the idle lifetime, for work the owner is not doing: a live stream must not
        keep its session alive (ADR-0031)."""
        with self._sessions.locked() as rows:
            row = self._authenticated(rows, binding, session_id, None, renew=False)
            remaining = ABSOLUTE_LIFETIME - (self._clock.now() - row.created)
            return SessionInfo(row.csrf_token, int(remaining.total_seconds()))

    def logout(self, binding: BrowserBinding, session_id: str, csrf: str) -> None:
        if not csrf:
            raise AuthenticationFailed()
        with self._sessions.locked() as rows:
            self._authenticated(rows, binding, session_id, csrf)
            del rows[_digest(session_id)]

    def _authenticated(
        self,
        rows: dict[bytes, StoredSession],
        binding: BrowserBinding,
        session_id: str,
        csrf: str | None,
        *,
        renew: bool = True,
    ) -> StoredSession:
        key = _digest(session_id)
        row = rows.get(key)
        if row is None or row.binding != binding:
            raise AuthenticationFailed()
        now = self._clock.now()
        if row.expired(now) or row.generation != self._credentials.generation():
            del rows[key]
            raise AuthenticationFailed()
        if csrf is not None and (
            len(csrf) > 128 or not csrf.isascii() or not hmac.compare_digest(row.csrf_token, csrf)
        ):
            raise CsrfRejected()
        if renew:
            row.last_used = now
        return row
