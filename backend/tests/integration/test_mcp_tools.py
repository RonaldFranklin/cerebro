"""The read tools over the real application with synthetic content and authenticated contexts."""

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from mcp import MCPError, types
from mcp.client import Client

from cerebro.adapters.inbound.mcp_tools import ReadTools, parse_tool
from cerebro.application.dto import GetNeighborsRequest, SearchNotesRequest
from cerebro.application.errors import AuthenticationFailed
from cerebro.bootstrap.container import Container, build_container
from cerebro.domain.access import AccessCeiling, AccessContext, Agent, Scope
from cerebro.domain.enums import Priority, Sensitivity, Status
from tests.integration.content_kit import HUB_ID, RESTRICTED_MARKER, SSH_ID, VAULT_ID, make_sample_repo

AGENT = AccessContext(
    principal=Agent("reader"), ceiling=AccessCeiling(Sensitivity.INTERNAL), scopes=frozenset({Scope.READ})
)


@pytest.fixture
def container(tmp_path: Path) -> Container:
    app = build_container(make_sample_repo(tmp_path).settings)
    app.start()
    return app


def tools_for(app: Container) -> ReadTools:
    cases = app.use_cases
    return ReadTools(
        get_overview=cases.get_overview,
        search_notes=cases.search_notes,
        read_note=cases.read_note,
        get_neighbors=cases.get_neighbors,
    )


def test_discovery_and_projections(container: Container) -> None:
    tools = tools_for(container)
    assert {t.name for t in tools.list_tools().tools} == {
        "cerebro_overview",
        "cerebro_search",
        "cerebro_read",
        "cerebro_neighbors",
    }
    for name, args in [
        ("cerebro_overview", {}),
        ("cerebro_search", {"query": ""}),
        ("cerebro_read", {"id": HUB_ID.value}),
        ("cerebro_neighbors", {"id": SSH_ID.value, "depth": 2}),
    ]:
        result = tools.call(name, args, lambda: AGENT)
        assert not result.is_error
        assert RESTRICTED_MARKER not in result.model_dump_json()
        assert VAULT_ID.value not in result.model_dump_json()
        assert isinstance(result.content[0], types.TextContent)
        assert json.loads(result.content[0].text) == result.structured_content
    hits = tools.call("cerebro_search", {"query": ""}, lambda: AGENT).structured_content["results"]
    assert len(hits) == 5
    assert set(hits[0]) == {"id", "type", "area", "title", "priority", "status", "summary"}
    neighbors = tools.call("cerebro_neighbors", {"id": SSH_ID.value, "depth": 2}, lambda: AGENT).structured_content
    assert any(n["id"] == "core-rules" for n in neighbors["neighbors"])
    assert all(n["summary"] and n["relation"] for n in neighbors["neighbors"])
    section = tools.call("cerebro_read", {"id": SSH_ID.value, "section": "SSH"}, lambda: AGENT)
    assert "Connect with the workstation key" in section.structured_content["body"]
    for tool in ("cerebro_read", "cerebro_neighbors"):
        hidden = tools.call(tool, {"id": VAULT_ID.value}, lambda: AGENT)
        assert hidden.is_error
        assert hidden == tools.call(tool, {"id": "absent"}, lambda: AGENT)


def test_arguments_normalize_before_authentication(container: Container) -> None:
    request = parse_tool(
        "cerebro_search",
        {
            "query": "é" * 512,
            "area": "infra",
            "type": "guide",
            "priority": "P2",
            "status": "current",
            "include_history": True,
        },
    )
    assert isinstance(request, SearchNotesRequest)
    assert request.limit == 5
    assert request.include_history
    assert request.priorities == {Priority.P2}
    assert request.statuses == {Status.CURRENT}
    near = parse_tool("cerebro_neighbors", {"id": "example"})
    assert isinstance(near, GetNeighborsRequest)
    assert near.hops == 1

    def forbidden() -> AccessContext:
        pytest.fail("invalid arguments must be rejected before authenticating or running a use case")

    adapter = tools_for(container)
    for name, args in [
        ("cerebro_search", {"query": "é" * 513}),
        ("cerebro_search", {"query": "", "area": "é" * 129}),
        ("cerebro_read", {"id": "note", "section": "é" * 129}),
        ("cerebro_read", {"id": "x", "extra": True}),
        ("cerebro_search", {"query": "", "limit": False}),
        ("cerebro_overview", {"ceiling": "restricted"}),
        ("cerebro_neighbors", {"id": "note", "depth": 3}),
        ("cerebro_neighbors", {"id": "note", "depth": True}),
        ("cerebro_propose", {}),
        ("cerebro_search", {"query": None}),
    ]:
        assert adapter.call(name, args, forbidden).is_error


def test_all_calls_require_agent_read_scope(container: Container) -> None:
    ctx = AccessContext(
        principal=Agent("writer"), ceiling=AccessCeiling(Sensitivity.INTERNAL), scopes=frozenset({Scope.PROPOSE})
    )
    adapter = tools_for(container)
    for name, args in [
        ("cerebro_overview", {}),
        ("cerebro_read", {"id": "note"}),
        ("cerebro_search", {"query": ""}),
        ("cerebro_neighbors", {"id": "note"}),
    ]:
        assert adapter.call(name, args, lambda: ctx).is_error


def test_previous_index_schema_is_rebuilt_from_content(container: Container) -> None:
    with sqlite3.connect(container.settings.state_dir / "index.sqlite") as db:
        db.execute("ALTER TABLE graph_nodes DROP COLUMN summary")
        db.execute("PRAGMA user_version=1")
    restored = build_container(container.settings)
    assert restored.start()
    result = tools_for(restored).call("cerebro_neighbors", {"id": SSH_ID.value}, lambda: AGENT)
    assert not result.is_error
    assert all(n["summary"] for n in result.structured_content["neighbors"])


def test_sdk_client_discovers_and_calls_registered_handlers(container: Container) -> None:
    checks = 0

    def authenticate(request: object) -> AccessContext:
        nonlocal checks
        checks += 1
        return AGENT

    async def run() -> None:
        async with Client(tools_for(container).server(authenticate)) as client:
            listed = await client.list_tools()
            assert len(listed.tools) == 4
            result = await client.call_tool("cerebro_read", {"id": SSH_ID.value})
            assert result.structured_content["id"] == SSH_ID.value
            assert "content_hash" not in result.structured_content
            rejected = await client.call_tool("cerebro_neighbors", {"id": SSH_ID.value, "depth": 3})
            assert rejected.is_error

    asyncio.run(run())
    assert checks == 2


def test_sdk_discovery_reauthenticates_even_after_a_success(container: Container) -> None:
    current: AccessContext | None = None

    def authenticate(request: object) -> AccessContext:
        if current is None:
            raise AuthenticationFailed
        return current

    async def run() -> None:
        nonlocal current
        async with Client(tools_for(container).server(authenticate)) as client:
            with pytest.raises(MCPError, match="Access denied"):
                await client.list_tools(cache_mode="bypass")
            current = AGENT
            assert len((await client.list_tools(cache_mode="bypass")).tools) == 4
            current = None
            with pytest.raises(MCPError, match="Access denied"):
                await client.list_tools(cache_mode="bypass")

    asyncio.run(run())
