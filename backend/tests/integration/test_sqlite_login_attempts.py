import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from cerebro.adapters.outbound.sqlite.login_attempts import SqliteLoginAttempts
from cerebro.application.ports.browser import LoginAttempts
from tests.contract.login_attempts_contract import NOW, LoginAttemptsContract


class TestSqliteLoginAttempts(LoginAttemptsContract):
    @pytest.fixture
    def attempts(self, tmp_path: Path) -> LoginAttempts:
        return SqliteLoginAttempts(tmp_path / "state.sqlite")


def test_distinct_stores_share_reservations_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    stores = [SqliteLoginAttempts(path) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        retries = pool.map(lambda store: store.reserve(NOW), stores)
    assert sorted(retries) == [0] * 5 + [900] * 7
    assert SqliteLoginAttempts(path).reserve(NOW) == 900
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT count(*) FROM login_attempts").fetchone() == (5,)
