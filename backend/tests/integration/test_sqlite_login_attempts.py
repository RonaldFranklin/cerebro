import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import timedelta
from pathlib import Path
from threading import Event

import pytest

from cerebro.adapters.outbound.sqlite._state_db import StateDatabase
from cerebro.adapters.outbound.sqlite.login_attempts import SqliteLoginAttempts
from cerebro.application.ports.browser import LoginAttempts
from tests.contract.login_attempts_contract import NOW, LoginAttemptsContract
from tests.fakes.system import FixedClock


class TestSqliteLoginAttempts(LoginAttemptsContract):
    @pytest.fixture
    def attempts(self, tmp_path: Path) -> LoginAttempts:
        return SqliteLoginAttempts(tmp_path / "state.sqlite")


def test_distinct_stores_share_reservations_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    stores = [SqliteLoginAttempts(path) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        retries = pool.map(lambda store: store.reserve(FixedClock(NOW)), stores)
    assert sorted(retries) == [0] * 5 + [900] * 7
    assert SqliteLoginAttempts(path).reserve(FixedClock(NOW)) == 900
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT count(*) FROM login_attempts").fetchone() == (5,)


def test_waiting_reservation_uses_time_at_write_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state.sqlite"
    store = SqliteLoginAttempts(path)
    clock = FixedClock(NOW)
    for _ in range(5):
        assert store.reserve(clock) == 0
    waiting = Event()
    original = StateDatabase._open

    @contextmanager
    def traced(db: StateDatabase) -> Iterator[sqlite3.Connection]:
        with original(db) as connection:

            def on_statement(sql: str) -> None:
                if sql == "BEGIN IMMEDIATE":
                    waiting.set()

            connection.set_trace_callback(on_statement)
            yield connection

    monkeypatch.setattr(StateDatabase, "_open", traced)
    with ThreadPoolExecutor(max_workers=1) as pool, closing(sqlite3.connect(path)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        pending = pool.submit(store.reserve, clock)
        assert waiting.wait(timeout=3)
        clock.advance(timedelta(minutes=15))
        writer.commit()
        assert pending.result(timeout=3) == 0
