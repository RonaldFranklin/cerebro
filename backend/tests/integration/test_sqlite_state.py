"""Token storage, state file hardening and refusal of unsupported state files."""

import hashlib
import sqlite3
import stat
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest

from cerebro.adapters.outbound.sqlite import (
    SqliteTokenStore,
    StateCorruptionError,
    StateFileError,
    StateSchemaError,
)
from cerebro.adapters.outbound.sqlite._state_db import SCHEMA_VERSION, is_unique_violation
from cerebro.application.ports import TokenStore
from cerebro.domain.access import AccessCeiling, Scope
from cerebro.domain.agent_token import AgentToken
from cerebro.domain.enums import Sensitivity
from cerebro.domain.token_hash import TokenHash
from tests.contract.stores_contract import (
    T0,
    TokenStoreContract,
    make_token,
)


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "state.sqlite"


class TestSqliteTokenStoreContract(TokenStoreContract):
    @pytest.fixture
    def store(self, state_path: Path) -> TokenStore:
        return SqliteTokenStore(state_path)


def _sql(path: Path, statement: str, *params: object) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def test_file_and_directory_modes(state_path: Path) -> None:
    SqliteTokenStore(state_path).add(make_token())
    SqliteTokenStore(state_path)
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700
    for sidecar in (Path(f"{state_path}-wal"), Path(f"{state_path}-shm")):
        if sidecar.exists():
            assert not stat.S_IMODE(sidecar.stat().st_mode) & 0o077


def test_a_symlink_in_place_of_the_file_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.sqlite"
    real.touch(mode=0o600)
    link = tmp_path / "state.sqlite"
    link.symlink_to(real)
    with pytest.raises(StateFileError):
        SqliteTokenStore(link)


def test_a_file_more_open_than_0600_is_refused(state_path: Path) -> None:
    SqliteTokenStore(state_path)
    state_path.chmod(0o644)
    with pytest.raises(StateFileError):
        SqliteTokenStore(state_path)


def test_wal_and_foreign_keys_and_secure_delete(state_path: Path) -> None:
    SqliteTokenStore(state_path)
    with closing(sqlite3.connect(state_path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_an_unknown_schema_version_is_refused_and_the_file_is_kept(state_path: Path) -> None:
    SqliteTokenStore(state_path).add(make_token())
    _sql(state_path, "PRAGMA user_version = 99")
    with pytest.raises(StateSchemaError):
        SqliteTokenStore(state_path)
    _sql(state_path, f"PRAGMA user_version = {SCHEMA_VERSION}")
    assert SqliteTokenStore(state_path).get(make_token().id) is not None


def test_a_damaged_file_is_reported_and_never_deleted(state_path: Path) -> None:
    state_path.parent.mkdir(mode=0o700)
    state_path.write_bytes(b"this is not a database" * 100)
    state_path.chmod(0o600)
    with pytest.raises(StateCorruptionError):
        SqliteTokenStore(state_path)
    assert state_path.read_bytes().startswith(b"this is not a database")


def test_a_foreign_database_without_a_version_is_not_touched(state_path: Path) -> None:
    state_path.parent.mkdir(mode=0o700)
    state_path.touch(mode=0o600)
    _sql(state_path, "CREATE TABLE other (x)")
    with pytest.raises(StateSchemaError):
        SqliteTokenStore(state_path)


def test_token_hash_is_not_recoverable_from_the_store_repr(state_path: Path) -> None:
    store = SqliteTokenStore(state_path)
    token = make_token()
    store.add(token)
    digest = token.token_hash.hexdigest
    stored = store.get(token.id)
    assert stored is not None
    for text in (repr(store), repr(stored), str(stored), f"{stored}"):
        assert digest not in text


def test_token_roundtrip_with_every_field(state_path: Path) -> None:
    store = SqliteTokenStore(state_path)
    token = AgentToken(
        id="t-1",
        agent="codex@workstation",
        ceiling=AccessCeiling(Sensitivity.RESTRICTED),
        scopes=frozenset({Scope.READ, Scope.WRITE, Scope.CORE}),
        token_hash=TokenHash(hashlib.sha256(b"x").hexdigest()),
        created_at=T0,
        expires_at=T0 + timedelta(days=30),
    ).revoke(T0 + timedelta(days=1))
    store.add(token)
    assert store.get("t-1") == token
    assert store.find_by_hash(token.token_hash) == token
    assert store.all() == (token,)


def test_a_damaged_token_row_is_reported(state_path: Path) -> None:
    store = SqliteTokenStore(state_path)
    store.add(make_token())
    _sql(state_path, "UPDATE tokens SET ceiling = 'galaxy'")
    with pytest.raises(StateCorruptionError):
        store.get("token-0001")


PRIVATE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmU\n-----END OPENSSH PRIVATE KEY-----\n"
)


def test_every_fresh_connection_has_the_safety_pragmas(state_path: Path) -> None:
    store = SqliteTokenStore(state_path)
    with store._db.read() as conn:
        assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_an_existing_parent_directory_is_never_chmoded(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    with pytest.raises(StateFileError):
        SqliteTokenStore(parent / "state.sqlite")
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert not (parent / "state.sqlite").exists()


def test_a_file_replaced_by_a_symlink_is_refused_on_the_next_operation(state_path: Path, tmp_path: Path) -> None:
    store = SqliteTokenStore(state_path)
    store.add(make_token())
    other = tmp_path / "other.sqlite"
    other.touch(mode=0o600)
    state_path.rename(tmp_path / "moved.sqlite")
    state_path.symlink_to(other)
    with pytest.raises(StateFileError):
        store.get(make_token().id)


def test_a_file_swapped_for_another_regular_file_is_refused(state_path: Path) -> None:
    store = SqliteTokenStore(state_path)
    state_path.rename(state_path.with_name("moved.sqlite"))
    state_path.touch(mode=0o600)
    with pytest.raises(StateFileError):
        store.get(make_token().id)


def test_only_unique_violations_mean_already_exists() -> None:
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("CREATE TABLE t (a TEXT NOT NULL, b TEXT UNIQUE)")
        with pytest.raises(sqlite3.IntegrityError) as not_null:
            conn.execute("INSERT INTO t (a) VALUES (NULL)")
        assert not is_unique_violation(not_null.value)
        conn.execute("INSERT INTO t VALUES ('x', 'y')")
        with pytest.raises(sqlite3.IntegrityError) as unique:
            conn.execute("INSERT INTO t VALUES ('x', 'y')")
        assert is_unique_violation(unique.value)


def test_token_digest_roundtrips_and_a_damaged_row_fails_the_hash_lookup(state_path: Path) -> None:
    store = SqliteTokenStore(state_path)
    token = make_token()
    store.add(token)
    found = store.find_by_hash(token.token_hash)
    assert found is not None
    assert found.token_hash.hexdigest == token.token_hash.hexdigest
    _sql(state_path, "UPDATE tokens SET scopes = 'not json'")
    with pytest.raises(StateCorruptionError):
        store.find_by_hash(token.token_hash)


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_a_state_file_from_an_earlier_release_is_refused_and_left_intact(state_path: Path, version: int) -> None:
    # The releases with the retired Inbox wrote versions 1-4 and a proposals table; no upgrade exists (ADR-0023).
    state_path.parent.mkdir(mode=0o700)
    state_path.touch(mode=0o600)
    with closing(sqlite3.connect(state_path)) as db, db:
        db.execute("CREATE TABLE proposals (id TEXT PRIMARY KEY, payload BLOB NOT NULL) STRICT")
        db.execute("INSERT INTO proposals VALUES ('fixture', x'00')")
        db.execute(f"PRAGMA user_version = {version}")
    with pytest.raises(StateSchemaError, match=f"schema version {version};.*move the file aside") as error:
        SqliteTokenStore(state_path)
    assert "fixture" not in str(error.value)
    with closing(sqlite3.connect(state_path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (version,)
        assert db.execute("SELECT id FROM proposals").fetchall() == [("fixture",)]


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_unrecognized_state_file_is_left_intact(state_path: Path, version: int) -> None:
    state_path.parent.mkdir(mode=0o700)
    state_path.touch(mode=0o600)
    with closing(sqlite3.connect(state_path)) as db, db:
        db.execute("CREATE TABLE unrelated (value TEXT)")
        db.execute("INSERT INTO unrelated VALUES ('fixture')")
        db.execute(f"PRAGMA user_version = {version}")
    with pytest.raises(StateSchemaError):
        SqliteTokenStore(state_path)
    with closing(sqlite3.connect(state_path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (version,)
        assert db.execute("SELECT * FROM unrelated").fetchall() == [("fixture",)]
