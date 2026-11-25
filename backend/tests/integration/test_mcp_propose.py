"""Phase 4 MCP wire contract over both authenticated transports."""

import asyncio
import json
from pathlib import Path

import httpx2
import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from cerebro.adapters.inbound.mcp_tools import InvalidToolArguments, parse_tool
from cerebro.application.dto import IssueAgentTokenRequest, SubmitProposalRequest
from cerebro.bootstrap.container import build_container
from cerebro.domain.access import AccessCeiling, AccessContext, Scope
from cerebro.domain.enums import Sensitivity
from tests.integration.content_kit import BROKEN_ID, HUB_ID, SSH_ID, VAULT_ID, make_sample_repo
from tests.integration.test_cli_bootstrap import SAMPLE
from tests.integration.test_mcp_transports import BRIDGE, call, free_port, post, serving

PROPOSE = "cerebro_propose"
CREATE: dict[str, object] = {
    "kind": "create",
    "title": "Workspace guidelines",
    "body": "Private fixture body marker.",
    "suggested": {
        "area": "infrastructure",
        "type": "guide",
        "priority": "P2",
        "sensitivity": "internal",
        "summary": "A synthetic summary of local workspace guidelines for the development instance.",
        "hub": HUB_ID.value,
    },
}


@pytest.mark.parametrize(
    "extra",
    [
        {"agent": "other"},
        {"token_id": "other"},
        {"source": {"agent": "other"}},
        {"source": {"session": None}},
        {"source": None},
        {"suggested": {"more": 0}},
        {"body": False},
        {"suggested": {"related": ["note"] * 33}},
        {"suggested": {"related": "note"}},
        {"base_hash": "x"},
        {"kind": "update"},
        {"source": {"context": "é" * 1000 + "x"}},
        {"body": "é" * 24576 + "x"},
        {"target": "extra"},
        {"body": "\ud800"},
        {"title": ""},
    ],
)
def test_closed_proposal_fields_and_budgets(extra: dict[str, object]) -> None:
    with pytest.raises(InvalidToolArguments, match="Invalid tool arguments"):
        parse_tool(PROPOSE, {**CREATE, **extra})


def test_parse_byte_boundaries_and_all_kinds() -> None:
    request = parse_tool(PROPOSE, {**CREATE, "body": "é" * 24576, "source": {"context": "é" * 1000}})
    assert isinstance(request, SubmitProposalRequest)
    assert request.source_context == "é" * 1000
    shapes: list[tuple[str, dict[str, object]]] = [
        ("update", {"body": "updated"}),
        ("link", {"suggested": {"related": ["other"]}}),
        ("deprecate", {}),
    ]
    for kind, fields in shapes:
        parsed = parse_tool(PROPOSE, {"kind": kind, "target": "note", "base_hash": "a" * 64, **fields})
        assert isinstance(parsed, SubmitProposalRequest)
        assert parsed.kind.value == kind


@pytest.mark.parametrize(
    ("group", "field", "limit", "unit"),
    [
        ("", "title", 480, "é"),
        ("", "rationale", 4096, "é"),
        ("suggested", "summary", 1200, "é"),
        ("source", "session", 2000, "é"),
        ("source", "context", 2000, "é"),
        ("suggested", "hub", 80, "a"),
        ("suggested", "area", 256, "a"),
        ("suggested", "type", 256, "a"),
        ("suggested", "superseded_by", 80, "a"),
    ],
)
def test_proposal_field_boundaries(group: str, field: str, limit: int, unit: str) -> None:
    text = unit * (limit // len(unit.encode()))

    def args(value: str) -> dict[str, object]:
        return {**CREATE, group: {field: value}} if group else {**CREATE, field: value}

    assert isinstance(parse_tool(PROPOSE, args(text)), SubmitProposalRequest)
    with pytest.raises(InvalidToolArguments):
        parse_tool(PROPOSE, args(text + "x"))


def test_related_limits_apply_to_count_and_each_id() -> None:
    def args(ids: list[object]) -> dict[str, object]:
        return {**CREATE, "suggested": {"related": ids}}

    assert isinstance(parse_tool(PROPOSE, args(["a" * 80] * 32)), SubmitProposalRequest)
    for invalid in (args(["a" * 81]), args([None]), args([{}]), args(["note"] * 33)):
        with pytest.raises(InvalidToolArguments):
            parse_tool(PROPOSE, invalid)


def test_tool_scopes_and_submission_transports(tmp_path: Path) -> None:
    app = build_container(make_sample_repo(tmp_path).settings)
    config = tmp_path / "instance.toml"
    config.write_text(SAMPLE.replace("keys/fernet.key", "secrets/cerebro.key") + "\nproposals_pending = 1\n")
    owner = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))

    def issue(*scopes: Scope) -> str:
        return app.use_cases.issue_agent_token.execute(
            IssueAgentTokenRequest(
                agent="tester", scopes=frozenset(scopes), ceiling=AccessCeiling(Sensitivity.INTERNAL)
            ),
            owner,
        ).secret

    writer = issue(Scope.PROPOSE)
    both = issue(Scope.READ, Scope.PROPOSE)
    reader = issue(Scope.READ)
    port = free_port()

    async def exercise(client: Client, *, reads: bool) -> None:
        names = {tool.name for tool in (await client.list_tools()).tools}
        read_names = {"cerebro_read", "cerebro_search", "cerebro_neighbors", "cerebro_overview"}
        assert names == ({PROPOSE} | read_names if reads else {PROPOSE})
        if reads:
            full = await client.call_tool("cerebro_read", {"id": SSH_ID.value})
            assert len(full.structured_content["base_hash"]) == 64
            for args in ({"id": SSH_ID.value, "section": "SSH"}, {"id": BROKEN_ID.value}):
                assert "base_hash" not in (await client.call_tool("cerebro_read", args)).structured_content

        def update(target: str) -> dict[str, object]:
            return {"kind": "update", "target": target, "base_hash": "0" * 64, "body": "synthetic"}

        hidden = await client.call_tool(PROPOSE, update(VAULT_ID.value))
        assert hidden.is_error
        assert hidden == await client.call_tool(PROPOSE, update("absent-note"))
        result = await client.call_tool(PROPOSE, CREATE)
        assert result.structured_content
        assert result.structured_content["valid"] is True
        assert set(result.structured_content) == {"proposal_id", "valid"}
        stored = app.proposals.get(result.structured_content["proposal_id"])
        assert stored
        assert stored.source_agent == "tester"
        limited = await client.call_tool(PROPOSE, CREATE)
        assert limited.is_error
        assert "Proposal limit reached." in limited.model_dump_json()

    async def run() -> None:
        async with (
            httpx2.AsyncClient(headers={"Authorization": "Bearer " + writer}, trust_env=False) as http,
            Client(streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http)) as client,
        ):
            await exercise(client, reads=False)
        async with Client(
            StdioServerParameters(command=BRIDGE, args=["--private-port", str(port)], env={"CEREBRO_MCP_TOKEN": both}),
            mode="legacy",
        ) as client:
            await exercise(client, reads=True)

    with serving(config, port):
        assert post(port, reader, call(PROPOSE, CREATE))[0] == 403
        for args in ({**CREATE, "source": {"agent": "injected"}}, {**CREATE, "title": "x" * 513}):
            status, body = post(port, "bad", call(PROPOSE, args))
            assert status == 400
            assert json.loads(body)["error"]["message"] == "Invalid tool arguments."
        asyncio.run(run())
    assert len(app.proposals.find()) == 2
    assert b"Private fixture body marker" not in config.with_suffix(".log").read_bytes()
