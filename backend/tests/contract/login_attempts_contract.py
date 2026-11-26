"""Shared contract for the instance-wide login attempt reservation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from cerebro.application.ports.browser import LoginAttempts

NOW = datetime(2026, 11, 25, 14, 30, 0, 123456, tzinfo=UTC)


class LoginAttemptsContract:
    def test_concurrent_reservations_share_five_slots(self, attempts: LoginAttempts) -> None:
        with ThreadPoolExecutor(max_workers=8) as pool:
            retries = list(pool.map(attempts.reserve, [NOW] * 8))
        assert sorted(retries) == [0] * 5 + [900] * 3

    def test_rolling_window_releases_only_expired_slots(self, attempts: LoginAttempts) -> None:
        assert attempts.reserve(NOW) == 0
        for _ in range(4):
            assert attempts.reserve(NOW + timedelta(seconds=10)) == 0
        assert attempts.reserve(NOW + timedelta(seconds=899, microseconds=999999)) == 1
        assert attempts.reserve(NOW + timedelta(seconds=900)) == 0
        assert attempts.reserve(NOW + timedelta(seconds=900)) == 10

    def test_rewind_returns_a_bounded_delay(self, attempts: LoginAttempts) -> None:
        for _ in range(5):
            assert attempts.reserve(NOW) == 0
        assert attempts.reserve(NOW - timedelta(days=2)) == 900
