"""Contract for the rolling-hour limit on authenticated agent writes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from cerebro.application.ports.write_attempts import WriteAttemptStore

NOW = datetime(2026, 11, 26, 9, 30, 0, 123456, tzinfo=UTC)


class WriteAttemptsContract:
    def test_windows_are_per_token_and_have_an_exact_boundary(self, attempts: WriteAttemptStore) -> None:
        assert attempts.reserve("a", NOW, 1)
        assert attempts.reserve("b", NOW, 1)
        assert not attempts.reserve("a", NOW + timedelta(hours=1, microseconds=-1), 1)
        assert attempts.reserve("a", NOW + timedelta(hours=1), 1)
        assert not attempts.reserve("b", NOW, 1)

    def test_competing_calls_reserve_at_most_the_limit(self, attempts: WriteAttemptStore) -> None:
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: attempts.reserve("one", NOW, 3), range(10)))
        assert results.count(True) == 3

    def test_clock_rewind_and_reduced_limit(self, attempts: WriteAttemptStore) -> None:
        for minutes in (0, 5, 10):
            assert attempts.reserve("a", NOW + timedelta(minutes=minutes), 3)
        assert attempts.reserve("a", NOW + timedelta(days=1), 3)
        assert not attempts.reserve("a", NOW + timedelta(minutes=5), 3)
        assert not attempts.reserve("a", NOW + timedelta(minutes=65), 2)
        assert attempts.reserve("a", NOW + timedelta(minutes=70), 2)
