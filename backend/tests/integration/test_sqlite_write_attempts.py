import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from cerebro.adapters.outbound.sqlite.write_attempts import SqliteWriteAttemptStore
from cerebro.application.ports.write_attempts import WriteAttemptStore
from tests.contract.write_attempts_contract import NOW, WriteAttemptsContract


class TestSqliteWriteAttempts(WriteAttemptsContract):
    @pytest.fixture
    def attempts(self, tmp_path: Path) -> WriteAttemptStore:
        return SqliteWriteAttemptStore(tmp_path / "state.sqlite")


def test_reopening_preserves_budget_and_refusals_add_no_rows(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    assert SqliteWriteAttemptStore(path).reserve("one", NOW, 1)
    assert not SqliteWriteAttemptStore(path).reserve("one", NOW, 1)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM write_attempts").fetchone() == (1,)
