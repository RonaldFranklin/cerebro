"""Token usage counters: a contract shared with the fake, and the hardening of the disposable database."""

import os
import sqlite3
import stat
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cerebro.adapters.outbound.sqlite.usage import SqliteUsageCounters
from cerebro.application.dto import USAGE_HOURS, TokenUsage
from cerebro.application.ports.usage import UsageCounters
from tests.fakes.usage import FakeUsageCounters

NOW = datetime(2026, 11, 27, 12, 30, tzinfo=UTC)


@pytest.fixture(params=["fake", "sqlite"])
def counters(request: pytest.FixtureRequest, tmp_path: Path) -> UsageCounters:
    return FakeUsageCounters() if request.param == "fake" else SqliteUsageCounters(tmp_path / "usage.sqlite")


def test_nothing_recorded_is_an_empty_summary(counters: UsageCounters) -> None:
    assert counters.summary(NOW) == {}


def test_uses_are_counted_per_token_and_hour(counters: UsageCounters) -> None:
    for at in (NOW, NOW - timedelta(minutes=20), NOW - timedelta(hours=1), NOW - timedelta(hours=23)):
        counters.record("token-a", at)
    counters.record("token-b", NOW)
    summary = counters.summary(NOW)
    assert summary is not None
    hourly = [0] * USAGE_HOURS
    hourly[-1], hourly[-2], hourly[0] = 2, 1, 1
    assert summary["token-a"] == TokenUsage(last_used=NOW, hourly=tuple(hourly))
    assert sum(summary["token-b"].hourly) == 1


def test_old_hours_leave_the_window_and_the_last_use_stays(counters: UsageCounters) -> None:
    old = NOW - timedelta(days=3)
    counters.record("token-a", old)
    counters.record("token-b", NOW)
    summary = counters.summary(NOW)
    assert summary is not None
    assert summary["token-a"] == TokenUsage(last_used=old, hourly=(0,) * USAGE_HOURS)


def test_an_older_report_never_moves_the_last_use_back(counters: UsageCounters) -> None:
    counters.record("token-a", NOW)
    counters.record("token-a", NOW - timedelta(minutes=5))
    summary = counters.summary(NOW)
    assert summary is not None
    assert summary["token-a"].last_used == NOW


def rows(path: Path) -> int:
    with closing(sqlite3.connect(path)) as db:
        return int(db.execute("SELECT count(*) FROM uses").fetchone()[0])


def test_rows_older_than_the_window_are_removed_on_write(tmp_path: Path) -> None:
    path = tmp_path / "usage.sqlite"
    counters = SqliteUsageCounters(path)
    counters.record("token-a", NOW - timedelta(hours=26))
    counters.record("token-a", NOW - timedelta(hours=24))
    counters.record("token-a", NOW)
    assert rows(path) == 2
    assert path.stat().st_mode & 0o777 == 0o600


def other_schema(path: Path) -> None:
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE uses (something TEXT)")
        db.execute("PRAGMA user_version = 99")
        db.commit()


def foreign_tables(path: Path) -> None:
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE unrelated (x)")
        db.commit()


@pytest.mark.parametrize(
    "damage",
    [lambda path: path.write_bytes(b"not a database" * 100), other_schema, foreign_tables],
    ids=["garbage", "other-version", "foreign-tables"],
)
def test_an_unknown_or_damaged_file_is_discarded_and_recreated(
    tmp_path: Path, damage: Callable[[Path], object]
) -> None:
    path = tmp_path / "usage.sqlite"
    damage(path)
    path.chmod(0o600)
    counters = SqliteUsageCounters(path)
    counters.record("token-a", NOW)
    summary = counters.summary(NOW)
    assert summary is not None
    assert sum(summary["token-a"].hourly) == 1


def test_damage_found_while_running_is_recovered(tmp_path: Path) -> None:
    path = tmp_path / "usage.sqlite"
    counters = SqliteUsageCounters(path)
    counters.record("token-a", NOW)
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    path.write_bytes(b"x" * 4096)
    counters.record("token-a", NOW)
    counters.record("token-a", NOW)
    summary = counters.summary(NOW)
    assert summary is not None
    assert summary["token-a"].last_used == NOW


def test_a_link_in_its_place_is_never_followed(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.write_bytes(b"untouched")
    path = tmp_path / "usage.sqlite"
    path.symlink_to(target)
    counters = SqliteUsageCounters(path)
    counters.record("token-a", NOW)
    assert target.read_bytes() == b"untouched"
    assert not path.is_symlink()
    assert stat.S_ISREG(path.lstat().st_mode)


@pytest.mark.parametrize("make", [lambda p: p.mkdir(), os.mkfifo], ids=["directory", "fifo"])
def test_something_that_cannot_be_replaced_loses_only_the_counters(
    tmp_path: Path, make: Callable[[Path], object]
) -> None:
    path = tmp_path / "usage.sqlite"
    make(path)
    counters = SqliteUsageCounters(path)
    counters.record("token-a", NOW)
    assert counters.summary(NOW) is None


def test_a_busy_database_drops_the_count_quickly_and_keeps_the_file(tmp_path: Path) -> None:
    path = tmp_path / "usage.sqlite"
    counters = SqliteUsageCounters(path)
    counters.record("token-a", NOW)
    with closing(sqlite3.connect(path, isolation_level=None)) as holder:
        holder.execute("BEGIN IMMEDIATE")
        started = datetime.now(UTC)
        counters.record("token-a", NOW)
        assert datetime.now(UTC) - started < timedelta(seconds=2)
        holder.execute("ROLLBACK")
    summary = counters.summary(NOW)
    assert summary is not None
    assert sum(summary["token-a"].hourly) == 1


def test_a_missing_directory_never_raises(tmp_path: Path) -> None:
    counters = SqliteUsageCounters(tmp_path / "absent" / "usage.sqlite")
    counters.record("token-a", NOW)
    assert counters.summary(NOW) is None


def wrong_tables_of_this_version(path: Path) -> None:
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE uses (something TEXT)")
        db.execute("PRAGMA user_version = 1")
        db.commit()


def test_the_right_version_with_other_tables_is_discarded(tmp_path: Path) -> None:
    path = tmp_path / "usage.sqlite"
    wrong_tables_of_this_version(path)
    path.chmod(0o600)
    counters = SqliteUsageCounters(path)
    counters.record("token-a", NOW)
    summary = counters.summary(NOW)
    assert summary is not None
    assert list(summary) == ["token-a"]


def test_a_second_name_for_another_database_is_removed_without_touching_it(tmp_path: Path) -> None:
    other = tmp_path / "state.sqlite"
    with closing(sqlite3.connect(other)) as db:
        db.execute("CREATE TABLE tokens (id TEXT)")
        db.commit()
    before = other.read_bytes()
    path = tmp_path / "usage.sqlite"
    path.hardlink_to(other)
    counters = SqliteUsageCounters(path)
    counters.record("token-a", NOW)
    assert other.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("state")) == ["state.sqlite"]
    assert path.stat().st_ino != other.stat().st_ino


def test_a_removed_file_is_noticed_and_recreated(tmp_path: Path) -> None:
    path = tmp_path / "usage.sqlite"
    counters = SqliteUsageCounters(path)
    counters.record("token-a", NOW)
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    counters.record("token-b", NOW)
    summary = counters.summary(NOW)
    assert summary is not None
    assert list(summary) == ["token-b"]
    assert path.is_file()
