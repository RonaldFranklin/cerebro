import pytest

from cerebro.application.ports.write_attempts import WriteAttemptStore
from tests.contract.write_attempts_contract import WriteAttemptsContract
from tests.fakes.write_attempts import FakeWriteAttemptStore


class TestFakeWriteAttempts(WriteAttemptsContract):
    @pytest.fixture
    def attempts(self) -> WriteAttemptStore:
        return FakeWriteAttemptStore()
