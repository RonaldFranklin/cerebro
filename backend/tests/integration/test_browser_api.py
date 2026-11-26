"""Session and input boundaries of one browser API listener."""

from collections.abc import Iterator

import pytest
from fastapi import Request
from starlette.testclient import TestClient

from cerebro.adapters.inbound.browser_api import browser_api
from cerebro.application.browser import BrowserAuth, BrowserBinding
from cerebro.domain.access import AccessCeiling
from cerebro.domain.enums import Sensitivity
from tests.fakes.login_attempts import FakeLoginAttempts
from tests.fakes.owner_credentials import FakeOwnerCredentials
from tests.fakes.system import FixedClock, SequentialIdGenerator
from tests.unit.application.test_browser_auth import FakeSessions

ORIGIN = "https://owner.example.invalid"
HEADERS = {"Origin": ORIGIN, "X-Cerebro-Request": "1"}
COOKIE = "__Host-cerebro_private"


@pytest.fixture
def auth() -> BrowserAuth:
    credentials = FakeOwnerCredentials()
    credentials.provision(b"example fixture")
    return BrowserAuth(credentials, FakeLoginAttempts(), FakeSessions(), FixedClock(), SequentialIdGenerator())


@pytest.fixture
def client(auth: BrowserAuth) -> Iterator[TestClient]:
    app = browser_api(auth, BrowserBinding("private", ORIGIN), AccessCeiling(Sensitivity.RESTRICTED))

    @app.post("/api/probe")
    def probe(request: Request) -> dict[str, object]:
        return {"owner": request.state.ctx.is_owner, "ceiling": request.state.ctx.ceiling.level.value}

    with TestClient(app, base_url=ORIGIN) as client:
        yield client


def test_browser_session_flow(client: TestClient) -> None:
    assert client.get("/api/session").status_code == 401
    response = client.post("/api/login", json={"password": "example fixture"}, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(COOKIE + "=")
    for attribute in ("HttpOnly", "Path=/", "SameSite=strict", "Secure"):
        assert attribute in cookie
    assert "Domain=" not in cookie
    session = response.json()
    assert session["ceiling"] == "restricted"
    assert session == client.get("/api/session").json()
    for headers in (
        {},
        {"Origin": ORIGIN},
        {"Origin": ORIGIN, "X-CSRF-Token": "wrong"},
        {"Origin": "https://other.example.invalid", "X-CSRF-Token": session["csrf_token"]},
    ):
        assert client.post("/api/probe", headers=headers).status_code == 403
    headers = {"Origin": ORIGIN, "X-CSRF-Token": session["csrf_token"], "X-Ceiling": "restricted"}
    assert client.post("/api/probe", headers=headers).json() == {"owner": True, "ceiling": "restricted"}
    assert client.post("/api/logout", headers=headers).status_code == 204
    assert client.get("/api/session").status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        '{"password":null}',
        '{"password":true}',
        '{"password":"marker","extra":"marker"}',
        '{"password":"old","password":"marker"}',
        '"marker"',
        '{"password":"\\ud800"}',
        '{"password":"' + "é" * 513 + '"}',
        "{" * 3000,
    ],
)
def test_login_fields_are_strict_and_never_echoed(client: TestClient, body: str) -> None:
    response = client.post("/api/login", content=body.encode(), headers={**HEADERS, "Content-Type": "application/json"})
    assert response.status_code == 422
    assert "marker" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_cross_site_and_ambiguous_login_are_refused(client: TestClient) -> None:
    for headers in (
        {"Origin": ORIGIN},
        {"X-Cerebro-Request": "1"},
        {**HEADERS, "Origin": "https://other.example.invalid"},
    ):
        assert client.post("/api/login", json={"password": "example fixture"}, headers=headers).status_code == 403
    duplicate = [("Origin", ORIGIN), ("Origin", ORIGIN), ("X-Cerebro-Request", "1")]
    assert client.post("/api/login", json={"password": "example fixture"}, headers=duplicate).status_code == 403
    assert client.post("/api/login", content="password=example", headers=HEADERS).status_code == 415
    assert client.post("/api/login?password=marker", json={}, headers=HEADERS).status_code == 422


def test_throttle_and_default_authentication(client: TestClient) -> None:
    assert client.post("/api/probe", headers=HEADERS).status_code == 401
    for _ in range(5):
        assert client.post("/api/login", json={"password": "wrong"}, headers=HEADERS).status_code == 401
    limited = client.post("/api/login", json={"password": "example fixture"}, headers=HEADERS)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "900"


def test_chunked_body_limit(client: TestClient) -> None:
    response = client.post(
        "/api/login", content=iter([b"x" * 32768] * 3), headers={**HEADERS, "Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.headers["cache-control"] == "no-store"


def test_sessions_are_bound_to_the_issuing_listener(client: TestClient, auth: BrowserAuth) -> None:
    client.post("/api/login", json={"password": "example fixture"}, headers=HEADERS)
    private_id = client.cookies[COOKIE]
    public_origin = ORIGIN + ":9443"
    public_cookie = "__Host-cerebro_public"
    app = browser_api(auth, BrowserBinding("public", public_origin), AccessCeiling(Sensitivity.INTERNAL))
    with TestClient(app, base_url=public_origin) as public:
        for name in (COOKIE, public_cookie):
            assert public.get("/api/session", headers={"Cookie": f"{name}={private_id}"}).status_code == 401
        headers = {**HEADERS, "Origin": public_origin, "X-Ceiling": "restricted"}
        response = public.post("/api/login", json={"password": "example fixture"}, headers=headers)
        assert response.json()["ceiling"] == "internal"
        assert response.headers["set-cookie"].startswith(public_cookie + "=")
        both = f"{COOKIE}={private_id}; {public_cookie}={public.cookies[public_cookie]}"
        assert public.get("/api/session", headers={"Cookie": both}).json()["ceiling"] == "internal"
        assert client.get("/api/session", headers={"Cookie": both}).json()["ceiling"] == "restricted"
        assert client.get("/api/session", headers={"Cookie": both + f"; {COOKIE}=other"}).status_code == 401
