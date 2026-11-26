import pytest

from cerebro.application.ports.browser import LoginAttempts
from tests.contract.login_attempts_contract import LoginAttemptsContract
from tests.fakes.login_attempts import FakeLoginAttempts


class TestFakeLoginAttempts(LoginAttemptsContract):
    @pytest.fixture
    def attempts(self) -> LoginAttempts:
        return FakeLoginAttempts()
