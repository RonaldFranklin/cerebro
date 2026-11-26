"""Invalid filters have fixed errors and never reach the use cases."""

from collections.abc import Iterator
from typing import Never

import pytest
from starlette.testclient import TestClient

from cerebro.adapters.inbound.browser_api import browser_api
from cerebro.adapters.inbound.browser_reads import BrowserReads, install_browser_reads
from cerebro.application.browser import BrowserAuth, BrowserBinding
from cerebro.domain.access import AccessCeiling
from cerebro.domain.enums import Sensitivity
from tests.fakes.login_attempts import FakeLoginAttempts
from tests.fakes.owner_credentials import FakeOwnerCredentials
from tests.fakes.system import FixedClock, SequentialIdGenerator
from tests.integration.test_browser_api import HEADERS, ORIGIN
from tests.unit.application.test_browser_auth import FakeSessions


class UnexpectedUseCase:
    def execute(self, request: object, ctx: object) -> Never:
        pytest.fail("Use case received invalid input")


@pytest.fixture
def api() -> Iterator[TestClient]:
    credentials = FakeOwnerCredentials()
    credentials.provision(b"fixture")
    auth = BrowserAuth(credentials, FakeLoginAttempts(), FakeSessions(), FixedClock(), SequentialIdGenerator())
    app = browser_api(auth, BrowserBinding("private", ORIGIN), AccessCeiling(Sensitivity.RESTRICTED))
    stub = UnexpectedUseCase()
    install_browser_reads(
        app,
        BrowserReads(
            get_overview=stub, search_notes=stub, read_note=stub, get_graph=stub, get_neighbors=stub, health_report=stub
        ),
    )
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post("/api/login", json={"password": "fixture"}, headers=HEADERS).is_success
        yield client


@pytest.mark.parametrize(
    "path",
    [
        "/api/search?" + "&" * 8200,
        "/api/search?limit=101",
        "/api/search?limit=1&limit=2",
        "/api/search?status=nope",
        "/api/search?unexpected=marker",
        "/api/search?area=Bad!",
        "/api/search?q=" + "é" * 513,
        "/api/overview?extra=1",
        "/api/health?extra=1",
        "/api/graph?history=garbage",
        "/api/notes/note/neighbors?hops=3",
        "/api/notes/not_a_slug",
        "/api/notes/note?section=",
    ],
)
def test_invalid_input_is_generic(api: TestClient, path: str) -> None:
    response = api.get(path)
    assert response.status_code == 422
    assert response.json() == {"error": "Invalid request."}
