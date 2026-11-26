"""Exercise the installed transports with temporary sockets, git, state and tokens."""

import asyncio
import http.client
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from cerebro.application.dto import IssueAgentTokenRequest, RevokeAgentTokenRequest
from cerebro.bootstrap.container import build_container
from cerebro.domain.access import AccessCeiling, AccessContext, Scope
from cerebro.domain.enums import Sensitivity
from tests.integration.content_kit import RESTRICTED_MARKER, SSH_ID, VAULT_ID, make_sample_repo
from tests.integration.test_cli_bootstrap import SAMPLE

SERVER = str(Path(sys.executable).with_name("cerebro-mcp"))
BRIDGE = str(Path(sys.executable).with_name("cerebro-mcp-stdio"))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def serving(
    config: Path, private: int, public: int | None = None, *, enable_public: bool = True, extra: tuple[str, ...] = ()
) -> Iterator[subprocess.Popen[bytes]]:
    args = [SERVER, "--config", str(config), "--private-port", str(private), *extra]
    if public is not None:
        args.extend(["--public-port", str(public)])
        if enable_public:
            args.append("--enable-public")
    with (
        config.with_suffix(".log").open("ab") as log,
        subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT) as process,
    ):
        try:
            for _ in range(200):
                if process.poll() is not None:
                    pytest.fail("MCP server exited before startup")
                try:
                    with socket.create_connection(("127.0.0.1", private), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("MCP server did not start")
            yield process
        finally:
            process.terminate()
            process.wait(timeout=10)


def post(port: int, secret: str, payload: bytes, *, path: str = "/mcp") -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": "Bearer " + secret,
            "X-Forwarded-For": "127.0.0.1",
        }
        # Iterable body deliberately selects chunked transfer, including for oversized requests.
        conn.request("POST", path, body=[payload[:1024], payload[1024:]], headers=headers, encode_chunked=True)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def call(name: str, args: dict[str, object]) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
    ).encode()


async def assert_read_contract(client: Client) -> None:
    names = {t.name for t in (await client.list_tools()).tools}
    calls: dict[str, dict[str, object]] = {
        "cerebro_overview": {},
        "cerebro_search": {"query": "SSH"},
        "cerebro_neighbors": {"id": SSH_ID.value, "depth": 2},
        "cerebro_read": {"id": SSH_ID.value},
    }
    assert names == calls.keys()
    for name, args in calls.items():
        result = await client.call_tool(name, args)
        assert not result.is_error
        assert result.structured_content
        assert RESTRICTED_MARKER not in result.model_dump_json()
        assert VAULT_ID.value not in result.model_dump_json()
    hidden = await client.call_tool("cerebro_read", {"id": VAULT_ID.value})
    absent = await client.call_tool("cerebro_read", {"id": "absent-note"})
    assert hidden.is_error
    assert hidden == absent


def test_real_listener_credentials_limits_and_logs(tmp_path: Path) -> None:
    sample = make_sample_repo(tmp_path)
    container = build_container(sample.settings)
    config = tmp_path / "instance.toml"
    config.write_text(SAMPLE.replace("keys/fernet.key", "secrets/cerebro.key"))
    owner = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))

    def issue(ceiling: Sensitivity, scope: Scope = Scope.READ) -> tuple[str, str]:
        row = container.use_cases.issue_agent_token.execute(
            IssueAgentTokenRequest(agent="fixture", scopes=frozenset({scope}), ceiling=AccessCeiling(ceiling)), owner
        )
        return row.token_id, row.secret

    tid, privileged = issue(Sensitivity.RESTRICTED)
    _, internal = issue(Sensitivity.INTERNAL)
    legacy_id, legacy = issue(Sensitivity.RESTRICTED)
    persisted = container.tokens.get(legacy_id)
    assert persisted is not None
    # Reproduce an already-issued credential from the Phase 4 store after upgrading.
    container.tokens.save(replace(persisted, scopes=frozenset({Scope.PROPOSE})))
    private, public = free_port(), free_port()
    read = call("cerebro_read", {"id": VAULT_ID.value})
    with serving(config, private, public):
        assert RESTRICTED_MARKER.encode() in post(private, privileged, read)[1]
        for port, secret in [(public, privileged), (private, internal)]:
            status, body = post(port, secret, read)
            assert status == 200
            assert b"Not found." in body
            assert body == post(port, secret, call("cerebro_read", {"id": "missing"}))[1]
        assert post(private, legacy, read)[0] == 403
        for secret in (internal, legacy):
            assert post(private, secret, call("cerebro_write_guide", {}))[0] == 403
            args: dict[str, object] = {
                "operation": "create",
                "request_id": "denied",
                "frontmatter": {},
                "body": "Synthetic note.",
            }
            assert post(private, secret, call("cerebro_write", args))[0] == 403
        assert post(private, legacy, call("cerebro_propose", {}))[0] == 400
        assert post(private, "invalid", read)[0] == 401
        assert post(private, privileged, read, path="/mcp?access_token=fixture-marker")[0] == 400
        assert post(private, "invalid", b"x" * 65537)[0] == 413
        invalid_status, invalid_body = post(private, "invalid", call("cerebro_search", {"query": "é" * 513}))
        assert invalid_status == 400
        assert json.loads(invalid_body) == {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32602, "message": "Invalid tool arguments."},
        }

        async def run_http() -> None:
            async with (
                httpx2.AsyncClient(headers={"Authorization": "Bearer " + internal}, trust_env=False) as http,
                Client(streamable_http_client(f"http://127.0.0.1:{private}/mcp", http_client=http)) as client,
            ):
                await assert_read_contract(client)
            async with (
                httpx2.AsyncClient(headers={"Authorization": "Bearer " + legacy}, trust_env=False) as http,
                Client(streamable_http_client(f"http://127.0.0.1:{private}/mcp", http_client=http)) as client,
            ):
                assert (await client.list_tools()).tools == []

        asyncio.run(run_http())
        container.use_cases.revoke_agent_token.execute(RevokeAgentTokenRequest(token_id=tid), owner)
        assert post(private, privileged, read)[0] == 401
    logs = config.with_suffix(".log").read_bytes()
    for value in (privileged, internal, legacy, RESTRICTED_MARKER, "fixture-marker"):
        assert value.encode() not in logs


def test_stdio_sdk_contract_and_offline(tmp_path: Path) -> None:
    sample = make_sample_repo(tmp_path)
    config = tmp_path / "dev.toml"
    config.write_text(SAMPLE.replace("keys/fernet.key", "secrets/cerebro.key"))
    private = free_port()
    token = build_container(sample.settings).use_cases.issue_agent_token.execute(
        IssueAgentTokenRequest(
            agent="client", scopes=frozenset({Scope.READ}), ceiling=AccessCeiling(Sensitivity.INTERNAL)
        ),
        AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED)),
    )
    cmd = ["--private-port", str(private)]
    env = {"CEREBRO_MCP_TOKEN": token.secret, "HTTP_PROXY": "http://invalid.invalid"}

    async def run(mode: str) -> None:
        async with Client(
            StdioServerParameters(command=BRIDGE, args=cmd, env=env), mode=mode, read_timeout_seconds=5
        ) as client:
            await assert_read_contract(client)

    unused_public = free_port()
    with serving(config, private, unused_public, enable_public=False):
        with pytest.raises(ConnectionRefusedError):
            socket.create_connection(("127.0.0.1", unused_public), timeout=1)
        asyncio.run(run("legacy"))
        asyncio.run(run("2026-07-28"))
    result = subprocess.run(
        [BRIDGE, *cmd],
        input=call("cerebro_overview", {}) + b"\n",
        capture_output=True,
        env={**os.environ, **env},
        timeout=8,
    )
    assert json.loads(result.stdout)["error"]["message"] == "Cérebro offline"
    assert not result.stderr


@pytest.mark.parametrize("args", [("--host", "0.0.0.0"), ("--public-port", "7654", "--private-port", "7654")])
def test_invalid_socket_configuration_rejected_before_wiring(args: tuple[str, ...]) -> None:
    result = subprocess.run([SERVER, *args], capture_output=True, timeout=5)
    assert result.returncode == 2
    assert b"invalid arguments" in result.stderr


def test_stdio_framing_and_invalid_calls_keep_the_request_id() -> None:
    from io import BytesIO

    from cerebro.adapters.inbound.mcp_stdio import bridge

    # A well-framed but invalid request receives a correlated error before opening a socket.
    for frame, expected in [
        (call("cerebro_neighbors", {"id": "note", "depth": 3}), "Invalid tool arguments."),
        (call("cerebro_overview", {}).ljust(65536), "Cérebro offline"),
    ]:
        output = BytesIO()
        assert bridge(BytesIO(frame + b"\n"), output, host="127.0.0.1", port=free_port(), secret="fixture") == 0
        error = json.loads(output.getvalue())
        assert error["id"] == 1
        assert error["error"]["message"] == expected
    output = BytesIO()
    assert bridge(BytesIO(b"x" * 65537 + b"\n"), output, host="127.0.0.1", port=free_port(), secret="fixture") == 2
    assert output.getvalue() == b""
    notification = b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
    assert bridge(BytesIO(notification), output, host="127.0.0.1", port=free_port(), secret="fixture") == 0
    assert output.getvalue() == b""


def test_sdk_gets_offline_error_when_token_store_fails(tmp_path: Path) -> None:
    from mcp import MCPError

    from cerebro.adapters.inbound.mcp_http import http_app
    from cerebro.application.dto import AuthenticateTokenRequest
    from tests.integration.test_mcp_tools import AGENT, tools_for

    class CredentialStore:
        online = True

        def execute(self, request: AuthenticateTokenRequest) -> AccessContext:
            if not self.online:
                raise RuntimeError("private backend diagnostic")
            return AGENT

    store = CredentialStore()
    router = http_app(tools_for(build_container(make_sample_repo(tmp_path).settings)), store, {8765: AGENT.ceiling})

    async def run() -> None:
        async with (
            router.lifespan_context(router),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=router), headers={"Authorization": "Bearer fixture"}
            ) as http,
            Client(
                streamable_http_client("http://127.0.0.1:8765/mcp", http_client=http), read_timeout_seconds=2
            ) as client,
        ):
            store.online = False
            with pytest.raises(MCPError, match="Cérebro offline"):
                await client.call_tool("cerebro_overview", {})

    asyncio.run(run())
