"""Phase 3 acceptance with the shipped executables, SDK clients and the owner's manual-test fixture."""

import asyncio
import json
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path

import httpx2
import pytest
from devtools.mcp_sample import create_sample
from mcp import MCPError
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from cerebro.application.dto import IssueAgentTokenRequest
from cerebro.application.use_cases import IssueAgentToken
from cerebro.bootstrap.container import build_container
from cerebro.bootstrap.settings import load_settings
from cerebro.domain.access import AccessCeiling, AccessContext, Scope
from cerebro.domain.enums import Sensitivity
from tests.fakes.system import FixedClock
from tests.integration.content_kit import RESTRICTED_MARKER, SSH_ID, VAULT_ID
from tests.integration.test_cli_bootstrap import invoke
from tests.integration.test_mcp_transports import BRIDGE, assert_read_contract, call, free_port, serving


def test_sdk_acceptance_and_recovery(tmp_path: Path) -> None:
    config = create_sample(tmp_path / "instance")
    owner = ["--config", str(config)]
    credential = invoke(*owner, "token", "issue", "fixture-agent", "--ceiling", "internal")
    assert credential.returncode == 0
    token = credential.stdout.strip()
    container = build_container(load_settings(config, {}))
    yesterday = FixedClock(container.clock.now() - timedelta(days=1))
    ceiling = AccessCeiling(Sensitivity.INTERNAL)
    expired = IssueAgentToken(container.tokens, yesterday, container.ids).execute(
        IssueAgentTokenRequest(
            agent="expired",
            ceiling=ceiling,
            scopes=frozenset({Scope.READ}),
            expires_at=yesterday.now() + timedelta(hours=1),
        ),
        AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED)),
    )
    port = free_port()
    url = f"http://127.0.0.1:{port}/mcp"

    async def check_visible(client: Client) -> None:
        await assert_read_contract(client)
        overview = await client.call_tool("cerebro_overview", {})
        assert overview.structured_content
        assert "core-rules" in {n["id"] for n in overview.structured_content["core"]}
        search = await client.call_tool("cerebro_search", {"query": "SSH"})
        assert search.structured_content
        assert SSH_ID.value in {n["id"] for n in search.structured_content["results"]}
        section = await client.call_tool("cerebro_read", {"id": SSH_ID.value, "section": "SSH"})
        assert section.structured_content
        assert "workstation key" in section.structured_content["body"]
        graph = await client.call_tool("cerebro_neighbors", {"id": SSH_ID.value, "depth": 2})
        assert graph.structured_content
        assert "core-rules" in {n["id"] for n in graph.structured_content["neighbors"]}
        query = await client.call_tool("cerebro_search", {"query": RESTRICTED_MARKER})
        assert query.structured_content == {"results": []}

    async def run() -> None:
        params = StdioServerParameters(
            command=BRIDGE, args=["--private-port", str(port)], env={"CEREBRO_MCP_TOKEN": token}
        )
        async with asyncio.timeout(40), httpx2.AsyncClient(trust_env=False, timeout=2) as http:
            http.headers["Authorization"] = "Bearer " + token
            with ExitStack() as listener:
                listener.enter_context(serving(config, port))
                async with Client(params, mode="legacy", read_timeout_seconds=4) as bridge:
                    async with Client(streamable_http_client(url, http_client=http)) as direct:
                        await check_visible(direct)
                    await check_visible(bridge)
                    listener.close()
                    with pytest.raises(MCPError, match="Cérebro offline"):
                        await bridge.call_tool("cerebro_read", {"id": SSH_ID.value})
                    with pytest.RaisesGroup(pytest.RaisesExc(httpx2.ConnectError), flatten_subgroups=True):
                        async with Client(streamable_http_client(url, http_client=http)) as direct:
                            await direct.list_tools()
                    listener.enter_context(serving(config, port))
                    await check_visible(bridge)
                    async with Client(streamable_http_client(url, http_client=http)) as direct:
                        await check_visible(direct)
                    body = call("cerebro_read", {"id": VAULT_ID.value})
                    for key in ("Last-Event-ID", "Mcp-Session-Id"):
                        response = await http.post(url, headers={key: "fixture"}, content=body)
                        assert response.status_code == 404
                    listed = json.loads(invoke(*owner, "token", "list").stdout)
                    token_id = next(row["token_id"] for row in listed if row["agent"] == "fixture-agent")
                    revoked = invoke(*owner, "token", "revoke", token_id)
                    assert revoked.returncode == 0
                    assert (await http.post(url, content=body)).status_code == 401
                    http.headers["Authorization"] = "Bearer " + expired.secret
                    assert (await http.post(url, content=body)).status_code == 401
                    del http.headers["Authorization"]
                    assert (await http.post(url, content=body)).status_code == 401

    asyncio.run(run())
