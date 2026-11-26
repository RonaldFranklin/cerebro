from contextlib import nullcontext
from datetime import timedelta
from hashlib import sha256

import pytest

from cerebro.adapters.outbound.system.sessions import MemorySessions
from cerebro.application.browser import MAX_SESSIONS, AuthenticationFailed, BrowserAuth, BrowserBinding, StoredSession
from cerebro.application.ports.browser import SessionStore
from tests.fakes.login_attempts import FakeLoginAttempts
from tests.fakes.owner_credentials import FakeOwnerCredentials
from tests.fakes.system import FixedClock, SequentialIdGenerator

PRIVATE = BrowserBinding("private", "https://private.example.invalid")


class FakeSessions:
    def __init__(self) -> None:
        self.rows: dict[bytes, StoredSession] = {}

    def locked(self) -> nullcontext[dict[bytes, StoredSession]]:
        return nullcontext(self.rows)


type Setup = tuple[BrowserAuth, FixedClock, FakeOwnerCredentials, SessionStore]


@pytest.fixture(params=[FakeSessions, MemorySessions])
def setup(request: pytest.FixtureRequest) -> Setup:
    clock = FixedClock()
    credentials = FakeOwnerCredentials()
    credentials.provision(b"fixture")
    sessions: SessionStore = request.param()
    auth = BrowserAuth(credentials, FakeLoginAttempts(), sessions, clock, SequentialIdGenerator())
    return auth, clock, credentials, sessions


def test_rotation_binding_and_logout(setup: Setup) -> None:
    auth, _, _, store = setup
    first = auth.login(PRIVATE, b"fixture")
    with store.locked() as rows:
        assert list(rows) == [sha256(first.session_id.encode()).digest()]
    assert first.session_id not in repr(first)
    for binding in (BrowserBinding("public", PRIVATE.origin), BrowserBinding("private", "https://other.invalid")):
        with pytest.raises(AuthenticationFailed):
            auth.session(binding, first.session_id)
    new = auth.login(PRIVATE, b"fixture", first.session_id)
    with pytest.raises(AuthenticationFailed):
        auth.session(PRIVATE, first.session_id)
    assert auth.session(PRIVATE, new.session_id) == new.csrf_token
    with pytest.raises(AuthenticationFailed):
        auth.logout(PRIVATE, new.session_id, "wrong")
    auth.logout(PRIVATE, new.session_id, new.csrf_token)
    with pytest.raises(AuthenticationFailed):
        auth.session(PRIVATE, new.session_id)


def test_login_limits(setup: Setup) -> None:
    auth, clock, _, _ = setup
    for password in (b"", b"wrong", b"x" * 1025, b"wrong", b"wrong"):
        with pytest.raises(AuthenticationFailed):
            auth.login(PRIVATE, password)
    with pytest.raises(AuthenticationFailed) as caught:
        auth.login(PRIVATE, b"fixture")
    assert caught.value.retry_after == 900
    clock.advance(timedelta(minutes=15))
    assert auth.login(PRIVATE, b"fixture")


def test_expiry(setup: Setup) -> None:
    auth, clock, _, _ = setup
    first = auth.login(PRIVATE, b"fixture")
    clock.advance(timedelta(minutes=30))
    with pytest.raises(AuthenticationFailed):
        auth.session(PRIVATE, first.session_id)
    second = auth.login(PRIVATE, b"fixture")
    for _ in range(23):
        clock.advance(timedelta(minutes=20))
        assert auth.session(PRIVATE, second.session_id, csrf=second.csrf_token)
    clock.advance(timedelta(minutes=20))
    with pytest.raises(AuthenticationFailed):
        auth.session(PRIVATE, second.session_id)


def test_reset(setup: Setup) -> None:
    auth, _, credentials, _ = setup
    first = auth.login(PRIVATE, b"fixture")
    credentials.provision(b"fixture")
    with pytest.raises(AuthenticationFailed):
        auth.session(PRIVATE, first.session_id)


def test_bounded_store_and_restart(setup: Setup) -> None:
    auth, clock, credentials, store = setup
    first = auth.login(PRIVATE, b"fixture")
    # Simulate independent successful admissions to exercise the session cap without
    # the unrelated rolling limit expiring the oldest session during the test.
    ids = SequentialIdGenerator()
    ids.new_token_secret()
    ids.new_token_secret()
    for _ in range(MAX_SESSIONS):
        clock.advance(timedelta(seconds=1))
        auth = BrowserAuth(credentials, FakeLoginAttempts(), store, clock, ids)
        latest = auth.login(PRIVATE, b"fixture", "malformed")
    with store.locked() as rows:
        assert len(rows) == MAX_SESSIONS
    with pytest.raises(AuthenticationFailed):
        auth.session(PRIVATE, first.session_id)
    assert auth.session(PRIVATE, latest.session_id) == latest.csrf_token
    restart = BrowserAuth(credentials, FakeLoginAttempts(), MemorySessions(), clock, ids)
    with pytest.raises(AuthenticationFailed):
        restart.session(PRIVATE, latest.session_id)


def test_local_provision_rejects_invalid_password_before_rotation() -> None:
    from cerebro.application.browser import ProvisionOwnerPassword

    credentials = FakeOwnerCredentials()
    provision = ProvisionOwnerPassword(credentials)
    provision.execute(b"valid")
    generation = credentials.generation()
    for invalid in (b"", b"x" * 1025):
        with pytest.raises(ValueError, match="password size"):
            provision.execute(invalid)
    assert credentials.verify(b"valid") == generation
