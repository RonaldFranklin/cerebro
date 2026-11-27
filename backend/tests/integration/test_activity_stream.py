"""The live activity stream over a real service: authorization, delivery, bounds and endings."""

import http.client
import json
import ssl
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import pytest

from cerebro.application.dto import IssueAgentTokenRequest
from cerebro.bootstrap.browser import owner_credentials
from cerebro.bootstrap.container import build_container
from cerebro.domain.access import AccessCeiling, AccessContext, Scope
from cerebro.domain.enums import Sensitivity
from tests.integration.browser_tls import certificate, tls_relay
from tests.integration.content_kit import SSH_ID, VAULT_ID, make_sample_repo
from tests.integration.test_cli_bootstrap import SAMPLE
from tests.integration.test_mcp_transports import call, post, reserve_ports, serving

PASSWORD = "synthetic-owner-password"


class Service:
    def __init__(
        self, context: ssl.SSLContext, relays: dict[str, int], ports: dict[str, int], token: str, process: Any
    ) -> None:
        self.context, self.relays, self.ports, self.token, self.process = context, relays, ports, token, process

    def request(
        self, listener: str, method: str, path: str, headers: dict[str, str], body: bytes | None = None
    ) -> tuple[http.client.HTTPSConnection, http.client.HTTPResponse]:
        conn = http.client.HTTPSConnection("localhost", self.relays[listener], context=self.context, timeout=8)
        conn.request(method, path, headers=headers, body=body)
        return conn, conn.getresponse()

    def login(self, listener: str) -> tuple[str, str]:
        origin = f"https://localhost:{self.relays[listener]}"
        headers = {"Origin": origin, "Content-Type": "application/json", "X-Cerebro-Request": "1"}
        conn, response = self.request(
            listener, "POST", "/api/login", headers, json.dumps({"password": PASSWORD}).encode()
        )
        try:
            assert response.status == 200
            csrf = json.loads(response.read())["csrf_token"]
            return response.headers["set-cookie"].split(";")[0], csrf
        finally:
            conn.close()

    def read_note(self, note_id: str) -> None:
        assert post(self.ports["private"], self.token, call("cerebro_read", {"id": note_id}))[0] == 200


@contextmanager
def service(tmp_path: Path, *, browser_ceiling: str = "restricted") -> Iterator[Service]:
    sample = make_sample_repo(tmp_path)
    owner_credentials(sample.settings).provision(PASSWORD.encode())
    container = build_container(sample.settings)
    token = container.use_cases.issue_agent_token.execute(
        IssueAgentTokenRequest(
            agent="live-agent",
            scopes=frozenset({Scope.READ}),
            ceiling=AccessCeiling(Sensitivity.RESTRICTED),
        ),
        AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED)),
    )
    config = tmp_path / "instance.toml"
    config.write_text(SAMPLE.replace("keys/fernet.key", "secrets/cerebro.key"))
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><head></head><body></body></html>")
    server_tls, client_tls = certificate(tmp_path)
    with ExitStack() as relays:
        with ExitStack() as reservation:
            private, public = reserve_ports(reservation, 2)
            addresses = {
                "private": relays.enter_context(tls_relay(private, server_tls)),
                "public": relays.enter_context(tls_relay(public, server_tls)),
            }
        extra = (
            *("--private-origin", f"https://localhost:{addresses['private']}"),
            *("--public-origin", f"https://localhost:{addresses['public']}"),
            *("--web-dist", str(dist), "--public-browser-ceiling", browser_ceiling),
        )
        with serving(config, private, public, extra=extra) as process:
            yield Service(client_tls, addresses, {"private": private, "public": public}, token.secret, process)
        log = config.with_suffix(".log").read_text()
        assert token.secret not in log


def next_event(response: http.client.HTTPResponse) -> dict[str, str]:
    while line := response.readline():
        if line.startswith(b"data: "):
            return dict(json.loads(line[6:]))
    raise AssertionError("the stream ended without an event")


def ended(response: http.client.HTTPResponse, within: float = 6.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not response.readline():
            return True
    return False


def test_agent_reads_reach_the_owner_with_id_verb_client_and_time_only(tmp_path: Path) -> None:
    with service(tmp_path) as live:
        cookie, _ = live.login("private")
        live.read_note(SSH_ID.value)
        conn, response = live.request("private", "GET", "/api/activity", {"Cookie": cookie})
        try:
            assert response.status == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-store"
            assert "connect-src 'self'" in response.headers["content-security-policy"]
            earlier = next_event(response)
            live.read_note(VAULT_ID.value)
            later = next_event(response)
        finally:
            conn.close()
        assert earlier.keys() == later.keys() == {"note_id", "verb", "client", "at"}
        assert (earlier["note_id"], later["note_id"]) == (SSH_ID.value, VAULT_ID.value)
        assert (later["verb"], later["client"]) == ("read", "live-agent")


def test_refusals_carry_no_stream(tmp_path: Path) -> None:
    with service(tmp_path, browser_ceiling="internal") as live:
        public_cookie, _ = live.login("public")
        private_cookie, _ = live.login("private")
        cases = [
            ("private", "GET", "/api/activity", {}, 401),
            ("public", "GET", "/api/activity", {"Cookie": private_cookie}, 401),
            ("public", "GET", "/api/activity", {"Cookie": public_cookie}, 403),
            ("private", "GET", "/api/activity?after=1", {"Cookie": private_cookie}, 422),
            ("private", "POST", "/api/activity", {"Cookie": private_cookie}, 405),
            ("private", "GET", "/api/activity", {"Cookie": private_cookie, "Origin": "https://other.invalid"}, 403),
            ("private", "GET", "/api/activity%0A", {"Cookie": private_cookie}, 422),
        ]
        for listener, method, path, headers, expected in cases:
            conn, response = live.request(listener, method, path, headers)
            try:
                assert response.status == expected, (listener, method, path)
                assert b"data:" not in response.read()
            finally:
                conn.close()


def test_open_streams_are_bounded_and_logout_ends_them(tmp_path: Path) -> None:
    with service(tmp_path) as live:
        cookie, csrf = live.login("private")
        streams = [live.request("private", "GET", "/api/activity", {"Cookie": cookie}) for _ in range(4)]
        try:
            assert [response.status for _, response in streams] == [200] * 4
            conn, fifth = live.request("private", "GET", "/api/activity", {"Cookie": cookie})
            assert fifth.status == 429
            conn.close()
            # An agent is served while the streams are open.
            live.read_note(SSH_ID.value)
            origin = f"https://localhost:{live.relays['private']}"
            headers = {"Origin": origin, "Cookie": cookie, "X-CSRF-Token": csrf}
            conn, response = live.request("private", "POST", "/api/logout", headers)
            assert response.status == 204
            conn.close()
            live.read_note(VAULT_ID.value)
            for _, stream in streams:
                body = b""
                while line := stream.readline():
                    body += line
                assert VAULT_ID.value.encode() not in body
        finally:
            for conn, _ in streams:
                conn.close()


def test_shutdown_ends_open_streams_quickly(tmp_path: Path) -> None:
    with service(tmp_path) as live:
        cookie, _ = live.login("private")
        conn, response = live.request("private", "GET", "/api/activity", {"Cookie": cookie})
        try:
            assert response.status == 200
            started = time.monotonic()
            live.process.terminate()
            assert ended(response)
            live.process.wait(timeout=8)
            assert time.monotonic() - started < 7
        finally:
            conn.close()


@pytest.mark.parametrize("scope", ["replayed"])
def test_a_replayed_create_is_marked_by_the_use_case(tmp_path: Path, scope: str) -> None:
    from cerebro.application.agent_write import AgentWriteRequest

    sample = make_sample_repo(tmp_path)
    container = build_container(sample.settings)
    container.start()
    owner = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))
    issued = container.use_cases.issue_agent_token.execute(
        IssueAgentTokenRequest(
            agent="writer",
            scopes=frozenset({Scope.READ, Scope.WRITE}),
            ceiling=AccessCeiling(Sensitivity.INTERNAL),
        ),
        owner,
    )
    from cerebro.application.dto import AuthenticateTokenRequest

    ctx = container.use_cases.authenticate_token.execute(
        AuthenticateTokenRequest(secret=issued.secret, listener=AccessCeiling(Sensitivity.RESTRICTED))
    )
    stored = container.notes.get(SSH_ID)
    assert stored
    request = AgentWriteRequest(
        operation="create",
        request_id="live-1",
        frontmatter={
            "title": "A live activity fixture",
            "type": stored.type.value,
            "area": stored.area.value,
            "priority": "P2",
            "sensitivity": "internal",
            "summary": "A synthetic note used to tell a first create from its replay.",
            "hub": stored.hub.value if stored.hub else None,
        },
        body="Body.\n",
    )
    first = container.use_cases.agent_write.execute(request, ctx)
    again = container.use_cases.agent_write.execute(request, ctx)
    assert (first.replayed, again.replayed) == (False, True)
    assert first.sensitivity is again.sensitivity is Sensitivity.INTERNAL
