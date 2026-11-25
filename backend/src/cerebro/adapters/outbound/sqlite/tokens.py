"""``SqliteTokenStore``: agent tokens in ``state.sqlite``, by hash only (the secret never reaches the store).

The SHA-256 hash is kept as hex text in a unique-indexed column and looked up by exact equality (it is the digest
of a 256-bit random secret, not guessable), then compared again in constant time (``TokenHash.__eq__``) after the
fetch. Token rows are plain, unauthenticated columns, protected only by the ``0600`` file mode: anyone who can
write the file can forge or edit a row (a MAC over the rows is an open question for the owner).
Hashes are never logged or put in an error message or ``repr``.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cerebro.adapters.outbound.sqlite._state_db import StateCorruptionError, StateDatabase, is_unique_violation
from cerebro.application.errors import AlreadyExists, TokenNotFound
from cerebro.domain.access import AccessCeiling, Scope
from cerebro.domain.agent_token import AgentToken
from cerebro.domain.enums import Sensitivity
from cerebro.domain.errors import DomainError
from cerebro.domain.token_hash import TokenHash

_COLUMNS = "id, token_hash, agent, ceiling, scopes, created_at, created_key, expires_at, revoked_at"


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _when(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)


class SqliteTokenStore:
    def __init__(self, path: Path | str) -> None:
        self._db = StateDatabase(path)

    def __repr__(self) -> str:
        return f"SqliteTokenStore({self._db!r})"

    def add(self, token: AgentToken) -> None:
        with self._db.write() as conn:
            if conn.execute("SELECT 1 FROM tokens WHERE id = ?", (token.id,)).fetchone():
                raise AlreadyExists("token", token.id)
            try:
                conn.execute(f"INSERT INTO tokens ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", self._row(token))
            except sqlite3.IntegrityError as error:
                if not is_unique_violation(error):
                    raise
                raise AlreadyExists("token hash", token.id) from None

    def get(self, token_id: str) -> AgentToken | None:
        with self._db.read() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM tokens WHERE id = ?", (token_id,)).fetchone()
        return None if row is None else self._load(row)

    def find_by_hash(self, token_hash: TokenHash) -> AgentToken | None:
        with self._db.read() as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM tokens WHERE token_hash = ?", (token_hash.hexdigest,)
            ).fetchone()
        if row is None:
            return None
        token = self._load(row)
        return token if token.token_hash == token_hash else None  # constant-time comparison

    def save(self, token: AgentToken) -> None:
        row = self._row(token)
        try:
            with self._db.write() as conn:
                cursor = conn.execute(
                    "UPDATE tokens SET token_hash = ?, agent = ?, ceiling = ?, scopes = ?, created_at = ?, "
                    "created_key = ?, expires_at = ?, revoked_at = ? WHERE id = ?",
                    (*row[1:], token.id),
                )
                if cursor.rowcount == 0:
                    raise TokenNotFound(token.id)
        except sqlite3.IntegrityError as error:
            if not is_unique_violation(error):
                raise
            raise AlreadyExists("token hash", token.id) from None

    def all(self) -> tuple[AgentToken, ...]:
        with self._db.read() as conn:
            rows = conn.execute(f"SELECT {_COLUMNS} FROM tokens ORDER BY created_key, id").fetchall()
        return tuple(self._load(row) for row in rows)

    # -- internals ---------------------------------------------------------------------------------------------

    @staticmethod
    def _row(t: AgentToken) -> tuple[Any, ...]:
        return (
            t.id,
            t.token_hash.hexdigest,
            t.agent,
            t.ceiling.level.value,
            json.dumps(sorted(s.value for s in t.scopes)),
            t.created_at.isoformat(),
            t.created_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            _iso(t.expires_at),
            _iso(t.revoked_at),
        )

    @staticmethod
    def _load(row: tuple[Any, ...]) -> AgentToken:
        token_id, digest, agent, ceiling, scopes, created, _key, expires, revoked = row
        try:
            return AgentToken(
                id=token_id,
                agent=agent,
                ceiling=AccessCeiling(Sensitivity(ceiling)),
                scopes=frozenset(Scope(s) for s in json.loads(scopes)),
                token_hash=TokenHash(digest),
                created_at=datetime.fromisoformat(created),
                expires_at=_when(expires),
                revoked_at=_when(revoked),
            )
        except TypeError, ValueError, DomainError:
            raise StateCorruptionError(f"token {token_id!r} has an unreadable record") from None
