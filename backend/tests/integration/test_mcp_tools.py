"""The read tools over the real application with synthetic content and authenticated contexts."""

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from mcp import MCPError, types
from mcp.client import Client

from cerebro.adapters.inbound.diagnostics import LOCAL_FAILURE, NOTE_FILES_FAILURE
from cerebro.adapters.inbound.mcp_tools import MCPTools, parse_tool
from cerebro.application.dto import GetNeighborsRequest, SearchNotesRequest
from cerebro.application.errors import AuthenticationFailed
from cerebro.bootstrap.container import Container, build_container
from cerebro.domain.access import AccessCeiling, AccessContext, Agent, Scope
from cerebro.domain.enums import Priority, Sensitivity, Status
from tests.integration.content_kit import DOCKER_ID, HUB_ID, RESTRICTED_MARKER, SSH_ID, VAULT_ID, make_sample_repo

AGENT = AccessContext(
    principal=Agent("reader"), ceiling=AccessCeiling(Sensitivity.INTERNAL), scopes=frozenset({Scope.READ})
)


@pytest.fixture
def container(tmp_path: Path) -> Container:
    app = build_container(make_sample_repo(tmp_path).settings)
    app.start()
    return app


def tools_for(app: Container) -> MCPTools:
    cases = app.use_cases
    return MCPTools(
        get_overview=cases.get_overview,
        search_notes=cases.search_notes,
        read_note=cases.read_note,
        get_neighbors=cases.get_neighbors,
        agent_write=cases.agent_write,
        get_vocabulary=cases.get_vocabulary,
    )


def test_discovery_and_projections(container: Container) -> None:
    tools = tools_for(container)
    assert {t.name for t in tools.list_tools(AGENT).tools} == {
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
    assert parse_tool("cerebro_neighbors", {"id": "example", "depth": 1.0}) == near
    assert parse_tool("cerebro_search", {"query": "", "limit": 5.0}) == parse_tool("cerebro_search", {"query": ""})

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
        ("cerebro_neighbors", {"id": "note", "depth": 1.5}),
        ("cerebro_unknown", {}),
        ("cerebro_search", {"query": None}),
    ]:
        assert adapter.call(name, args, forbidden).is_error


@pytest.mark.parametrize(
    ("name", "args", "message"),
    [
        ("cerebro_search", {"query": "", "limit": 500}, "limit must be an integer from 1 to 100"),
        ("cerebro_search", {"query": "", "limit": 0}, "limit must be an integer from 1 to 100"),
        ("cerebro_search", {}, "query must be a string of at most 1024 UTF-8 bytes"),
        ("cerebro_search", {"query": "é" * 513}, "query must be a string of at most 1024 UTF-8 bytes"),
        ("cerebro_search", {"query": "", "area": "Not Kebab"}, "area must be a kebab-case string of at most 256"),
        ("cerebro_search", {"query": "", "type": 5}, "type must be a kebab-case string of at most 256"),
        ("cerebro_search", {"query": "", "priority": "P9"}, "priority must be one of P0, P1, P2, P3"),
        ("cerebro_search", {"query": "", "status": "done"}, "status must be one of current, "),
        ("cerebro_search", {"query": "", "include_history": "yes"}, "include_history must be true or false"),
        ("cerebro_read", {"id": "Marker Value"}, "id must be a kebab-case note id of at most 80 characters"),
        ("cerebro_read", {"id": "x", "section": " "}, "section must be a non-blank string of at most 256 UTF-8 bytes"),
        ("cerebro_read", {"id": "x", "marker_key": 1}, "unknown argument; cerebro_read accepts id, section"),
        ("cerebro_overview", {"marker_key": 1}, "unknown argument; cerebro_overview accepts no arguments"),
        ("cerebro_neighbors", {"id": "x", "depth": 3}, "depth must be an integer from 1 to 2"),
        ("cerebro_read", [], "arguments must be an object"),
        ("marker_tool", {}, "unknown tool"),
    ],
)
def test_argument_errors_name_the_field_and_the_allowed_values(
    container: Container, name: str, args: object, message: str
) -> None:
    result = tools_for(container).call(name, args, lambda: AGENT)
    assert result.is_error
    (content,) = result.content
    assert isinstance(content, types.TextContent)
    assert content.text.startswith(f"Invalid tool arguments: {message}")
    assert "marker" not in content.text.casefold()


def test_a_missing_section_lists_the_headings_of_a_readable_note_only(container: Container) -> None:
    tools = tools_for(container)
    missing = tools.call("cerebro_read", {"id": SSH_ID.value, "section": "Nope"}, lambda: AGENT)
    assert missing.is_error
    assert missing.structured_content is not None
    assert missing.structured_content["error"] == "Section not found."
    assert "SSH" in missing.structured_content["headings"]
    assert missing.structured_content["headings_truncated"] is False
    (content,) = missing.content
    assert isinstance(content, types.TextContent)
    assert json.loads(content.text) == missing.structured_content

    hidden = tools.call("cerebro_read", {"id": VAULT_ID.value, "section": "Nope"}, lambda: AGENT)
    assert hidden == tools.call("cerebro_read", {"id": "absent", "section": "Nope"}, lambda: AGENT)
    assert hidden == tools.call("cerebro_read", {"id": "absent"}, lambda: AGENT)
    (content,) = hidden.content
    assert isinstance(content, types.TextContent)
    assert content.text == "Not found."


def test_all_calls_require_agent_read_scope(container: Container) -> None:
    ctx = AccessContext(
        principal=Agent("writer"), ceiling=AccessCeiling(Sensitivity.INTERNAL), scopes=frozenset({Scope.WRITE})
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
    with closing(sqlite3.connect(container.settings.state_dir / "index.sqlite")) as db, db:
        db.execute("ALTER TABLE graph_nodes DROP COLUMN summary")
        db.execute("PRAGMA user_version=1")
    restored = build_container(container.settings)
    assert restored.start()
    result = tools_for(restored).call("cerebro_neighbors", {"id": SSH_ID.value}, lambda: AGENT)
    assert not result.is_error
    docker = next(n for n in result.structured_content["neighbors"] if n["id"] == DOCKER_ID.value)
    assert docker["summary"] == "How containers are named, built and run on the home server, as plain conventions."
    assert {"source": SSH_ID.value, "target": DOCKER_ID.value, "kind": "related"} in docker["relation"]


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


def text_of(result: types.CallToolResult) -> str:
    (content,) = result.content
    assert isinstance(content, types.TextContent)
    return content.text


def test_local_failures_are_not_offline_and_reveal_nothing(
    container: Container, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tools = tools_for(container)
    root = container.settings.content_repo
    vault = root / f"notes/{VAULT_ID.value}.md"
    vault.write_bytes(vault.read_bytes() + b"\n")  # a restricted note edited by hand, not committed
    missing = text_of(tools.call("cerebro_read", {"id": "absent-note"}, lambda: AGENT))
    assert text_of(tools.call("cerebro_read", {"id": VAULT_ID.value}, lambda: AGENT)) == missing == "Not found."

    container.search.set_revision("rev-elsewhere")  # the next read rebuilds, which refuses uncommitted files
    failed = tools.call("cerebro_search", {"query": ""}, lambda: AGENT)
    assert failed.is_error
    assert text_of(failed) == NOTE_FILES_FAILURE
    assert capsys.readouterr().err == "cerebro: mcp cerebro_search failed: NoteReadError\n"

    vault.write_bytes(vault.read_bytes()[:-1])
    monkeypatch.setattr(container.graph, "neighbors", lambda *a, **k: (_ for _ in ()).throw(OSError(RESTRICTED_MARKER)))
    failed = tools.call("cerebro_neighbors", {"id": SSH_ID.value}, lambda: AGENT)
    assert text_of(failed) == LOCAL_FAILURE
    err = capsys.readouterr().err
    assert err == "cerebro: mcp cerebro_neighbors failed: OSError\n"
    assert RESTRICTED_MARKER not in err
