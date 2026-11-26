"""The real system adapters run the shared Clock / IdGenerator contracts plus what the contract cannot see."""

import re
from datetime import UTC, datetime, timedelta

import pytest

from cerebro.adapters.outbound.system import SystemIdGenerator, UtcClock
from cerebro.application.ports import Clock, IdGenerator
from tests.contract.stores_contract import ClockContract, IdGeneratorContract

ALPHABET = re.compile(r"[A-Za-z0-9_-]{1,64}")


class TestUtcClock(ClockContract):
    @pytest.fixture
    def clock(self) -> Clock:
        return UtcClock()

    def test_is_close_to_the_system_time(self, clock: Clock) -> None:
        assert abs(datetime.now(UTC) - clock.now()) < timedelta(seconds=5)

    def test_offset_is_utc(self, clock: Clock) -> None:
        assert clock.now().tzinfo is UTC


class TestSystemIdGenerator(IdGeneratorContract):
    @pytest.fixture
    def ids(self) -> IdGenerator:
        return SystemIdGenerator()

    def test_secret_has_at_least_256_bits(self, ids: IdGenerator) -> None:
        # token_urlsafe(32) is 32 bytes = 43 base64url characters.
        assert all(len(ids.new_token_secret()) >= 43 for _ in range(50))

    def test_many_ids_and_secrets_are_unique_and_valid(self, ids: IdGenerator) -> None:
        tokens = [ids.new_token_id() for _ in range(5000)]
        secrets_ = [ids.new_token_secret() for _ in range(5000)]
        assert len(set(tokens)) == len(set(secrets_)) == 5000
        assert all(ALPHABET.fullmatch(i) for i in tokens)
        assert all(re.fullmatch(r"[A-Za-z0-9_-]+", s) for s in secrets_)
