"""Run both browser listeners through test-only HTTPS relays, using real credentials and sockets."""

import http.client
import json
import ssl
import subprocess
from http.cookies import SimpleCookie
from pathlib import Path

import pytest

from cerebro.bootstrap.browser import owner_credentials
from tests.integration.browser_tls import certificate, tls_relay
from tests.integration.content_kit import VAULT_ID, make_sample_repo
from tests.integration.test_cli_bootstrap import SAMPLE
from tests.integration.test_mcp_transports import SERVER, free_ports, serving


def request(
    context: ssl.SSLContext, port: int, method: str, path: str, headers: dict[str, str], payload: bytes | None = None
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPSConnection("localhost", port, context=context, timeout=5)
    try:
        conn.request(method, path, headers=headers, body=payload)
        response = conn.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        conn.close()


# The public MCP ceiling never widens the owner's browser on the public listener beyond internal.
@pytest.mark.parametrize("public_ceiling", [(), ("--public-ceiling", "restricted")])
def test_two_https_origins_share_service_but_isolate_sessions(tmp_path: Path, public_ceiling: tuple[str, ...]) -> None:
    fixture = make_sample_repo(tmp_path)
    owner_credentials(fixture.settings).provision(b"synthetic-owner-password")
    config = tmp_path / "instance.toml"
    config.write_text(SAMPLE.replace("keys/fernet.key", "secrets/cerebro.key"))
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><head></head><body>Fixture browser</body></html>")
    server, client = certificate(tmp_path)
    private_port, public_port = free_ports(2)
    with tls_relay(private_port, server) as private, tls_relay(public_port, server) as public:
        origins = {p: f"https://localhost:{p}" for p in (private, public)}
        options = (
            *("--private-origin", origins[private], "--public-origin", origins[public]),
            *("--web-dist", str(dist), *public_ceiling),
        )
        with serving(config, private_port, public_port, extra=options):
            cookies: dict[int, str] = {}
            tokens: dict[int, str] = {}
            for port, role, ceiling in ((private, "private", "restricted"), (public, "public", "internal")):
                headers = {"Origin": origins[port], "Content-Type": "application/json", "X-Cerebro-Request": "1"}
                status, response, body = request(
                    client, port, "POST", "/api/login", headers, b'{"password":"synthetic-owner-password"}'
                )
                assert status == 200, (status, body)
                assert response["cache-control"] == "no-store"
                cookie = SimpleCookie()
                cookie.load(response["set-cookie"])
                value = cookie[f"__Host-cerebro_{role}"]
                assert value["secure"]
                assert value["httponly"]
                assert value["samesite"].lower() == "strict"
                assert value["path"] == "/"
                assert not value["domain"]
                cookies[port] = f"{value.key}={value.value}"
                tokens[port] = json.loads(body)["csrf_token"]
                assert json.loads(body)["ceiling"] == ceiling
            both = "; ".join(cookies.values())
            for port, ceiling in ((private, "restricted"), (public, "internal")):
                forwarded = {"X-Forwarded-Host": "privileged.invalid", "X-Forwarded-Proto": "https", "Cookie": both}
                status, _, body = request(client, port, "GET", "/api/session", forwarded)
                assert status == 200
                assert json.loads(body)["ceiling"] == ceiling
                status, _, graph = request(client, port, "GET", "/api/graph", forwarded)
                assert status == 200
                assert (VAULT_ID.value in {node["id"] for node in json.loads(graph)["nodes"]}) == (port == private)
                assert request(client, port, "GET", "/api/health", forwarded)[0] == (200 if port == private else 403)
            replay = cookies[private].replace("cerebro_private=", "cerebro_public=")
            assert request(client, public, "GET", "/api/session", {"Cookie": replay})[0] == 401
            assert request(client, private, "GET", "/", {"Host": "other.invalid"})[0] == 400
            assert request(client, private, "GET", "/", {"Origin": origins[public]})[0] == 403
            status, response, page = request(client, private, "GET", "/search", {})
            assert status == 200
            assert b"Fixture browser" in page
            nonce = response["content-security-policy"].split("'nonce-", 1)[1].split("'", 1)[0]
            assert nonce.encode() in page
            assert response["referrer-policy"] == "no-referrer"
            assert response["x-content-type-options"] == "nosniff"
            assert response["cache-control"] == "no-store"
            assert nonce.encode() not in request(client, private, "GET", "/", {})[2]
            assert request(client, private, "GET", "/absent", {})[0] == 404
            logout = {"Origin": origins[private], "Cookie": both}
            assert request(client, private, "POST", "/api/logout", logout)[0] == 403
            logout["X-CSRF-Token"] = tokens[private]
            assert request(client, private, "POST", "/api/logout", logout)[0] == 204
            assert request(client, private, "GET", "/api/session", {"Cookie": both})[0] == 401
            assert request(client, public, "GET", "/api/session", {"Cookie": both})[0] == 200
        log = config.with_suffix(".log").read_text()
        session_ids = [cookie.partition("=")[2] for cookie in cookies.values()]
        for secret in [*tokens.values(), *session_ids, "synthetic-owner-password"]:
            assert secret not in log


@pytest.mark.parametrize(
    "origin", ["http://localhost", "https://localhost/", "https://user@localhost", "https://host:0"]
)
def test_invalid_external_origins_refused_at_startup(origin: str) -> None:
    result = subprocess.run([SERVER, "--private-origin", origin], capture_output=True, timeout=5)
    assert result.returncode == 2
    assert b"invalid arguments" in result.stderr
