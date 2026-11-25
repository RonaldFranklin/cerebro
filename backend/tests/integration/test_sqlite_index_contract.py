"""The shared index contracts, run against the real SQLite adapters."""

from pathlib import Path

import pytest

from cerebro.adapters.outbound.sqlite import SqliteGraphIndex, SqliteSearchIndex
from cerebro.application.ports import GraphIndex, SearchIndex
from tests.contract.search_contract import GraphIndexContract, SearchIndexContract


class TestSqliteSearchIndex(SearchIndexContract):
    @pytest.fixture
    def index(self, tmp_path: Path) -> SearchIndex:
        return SqliteSearchIndex(tmp_path / "state" / "index.sqlite")


class TestSqliteGraphIndex(GraphIndexContract):
    @pytest.fixture
    def graph(self, tmp_path: Path) -> GraphIndex:
        return SqliteGraphIndex(tmp_path / "state" / "index.sqlite")
