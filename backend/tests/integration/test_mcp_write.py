"""Direct MCP tool dispatch over a real application, including safe wire errors."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp import types

from cerebro.adapters.inbound.mcp_tools import MCPTools
from cerebro.bootstrap.container import Container, build_container
from cerebro.domain.access import AccessContext, Scope
from tests.integration.content_kit import BROKEN_ID, DOCKER_ID, HUB_ID, SSH_ID, make_sample_repo
from tests.integration.test_mcp_tools import AGENT, tools_for

WRITER = replace(AGENT, token_id="unit-write", scopes=frozenset({Scope.READ, Scope.WRITE}))
CREATE = {
    "operation": "create",
    "request_id": "unit-1",
    "frontmatter": {
        "area": "infrastructure",
        "hub": "infrastructure",
        "title": "Synthetic test",
        "priority": "P2",
        "type": "guide",
        "sensitivity": "internal",
        "summary": "A short synthetic test of the direct writing contract for authenticated agents.",
    },
    "body": "Synthetic content.",
}


@pytest.fixture
def container(tmp_path: Path) -> Container:
    app = build_container(make_sample_repo(tmp_path).settings)
    app.start()
    return app


@pytest.mark.parametrize("scope", [Scope.READ, Scope.WRITE])
def test_discovery_and_invocation_use_the_same_grant(scope: Scope, container: Container) -> None:
    tools = tools_for(container)
    ctx = replace(WRITER, scopes=frozenset({scope}))
    listed = {tool.name for tool in tools.list_tools(ctx).tools}
    for name, args in (("cerebro_write", CREATE), ("cerebro_write_guide", {})):
        assert (name in listed) == (scope is Scope.WRITE)
        assert tools.call(name, args, lambda: ctx).is_error == (scope is not Scope.WRITE)


@pytest.mark.parametrize(
    ("extra", "field"),
    [
        ({"body": "é" * (24 * 1024 + 1)}, "body"),
        ({"frontmatter": {"related": ["ref"] * 33}}, "related"),
        ({"frontmatter": {"unsafe-field-marker": "value"}}, "frontmatter"),
        ({"request_id": "x" * 65}, "request_id"),
        ({"target": "forbidden"}, "request"),
        ({"author": "unsafe-field-marker"}, "request"),
        ({"target": None}, "request"),
        ({"operation": "delete"}, "operation"),
        ({"body": "-----BEGIN PRIVATE KEY-----"}, "body"),
    ],
)
def test_invalid_candidates_return_safe_field_hints(extra: dict[str, object], field: str, container: Container) -> None:
    tools = tools_for(container)
    before = container.notes.head()
    result = tools.call("cerebro_write", {**CREATE, **extra}, lambda: WRITER)
    assert result.is_error
    assert isinstance(result.content[0], types.TextContent)
    assert json.loads(result.content[0].text) == result.structured_content
    finding = result.structured_content["findings"][0]
    assert set(finding) == {"code", "field", "hint"}
    assert finding["field"] == field
    assert finding["hint"]
    assert "unsafe-field-marker" not in result.model_dump_json()
    assert "BEGIN PRIVATE KEY" not in result.model_dump_json()
    assert container.notes.head() == before


def test_base_hash_requires_complete_unredacted_read_and_write_authority(container: Container) -> None:
    tools = tools_for(container)
    note = container.notes.get(SSH_ID)
    assert note is not None
    result = tools.call("cerebro_read", {"id": SSH_ID.value}, lambda: WRITER)
    assert result.structured_content["body"] == note.body
    assert "base_hash" in result.structured_content


@pytest.mark.parametrize(
    ("args", "ctx"),
    [
        ({"id": SSH_ID.value, "section": "SSH"}, WRITER),
        ({"id": BROKEN_ID.value}, WRITER),
        ({"id": SSH_ID.value}, AGENT),
    ],
)
def test_partial_or_read_only_result_has_no_update_hash(
    container: Container, args: dict[str, str], ctx: AccessContext
) -> None:
    result = tools_for(container).call("cerebro_read", args, lambda: ctx)
    assert not result.is_error
    assert "base_hash" not in result.structured_content


_WRITABLE = (
    "title",
    "type",
    "area",
    "priority",
    "sensitivity",
    "status",
    "summary",
    "hub",
    "related",
    "superseded_by",
    "lang",
    "review_by",
)


def _read(tools: MCPTools, note_id: str) -> dict[str, Any]:
    result = tools.call("cerebro_read", {"id": note_id}, lambda: WRITER)
    assert not result.is_error
    assert result.structured_content is not None
    return result.structured_content


def _update(tools: MCPTools, note_id: str, body: str | None = None, **changes: object) -> types.CallToolResult:
    """Read the whole note as a writer and send it back with a new ``body`` or frontmatter ``changes``."""
    view = _read(tools, note_id)
    request = {
        "operation": "update",
        "target": note_id,
        "base_hash": view["base_hash"],
        "frontmatter": {**{field: view[field] for field in _WRITABLE}, **changes},
        "body": view["body"] if body is None else body,
    }
    return tools.call("cerebro_write", request, lambda: WRITER)


def test_notes_linking_archived_targets_and_hubs_stay_readable_and_updatable(container: Container) -> None:
    tools = tools_for(container)
    frontmatter = {**CREATE["frontmatter"], "related": [DOCKER_ID.value]}  # type: ignore[dict-item]
    created = tools.call(
        "cerebro_write", {**CREATE, "frontmatter": frontmatter, "body": "See [[docker-conventions]]."}, lambda: WRITER
    )
    assert not created.is_error, created.structured_content
    assert created.structured_content is not None
    note_id = created.structured_content["note_id"]

    for target in (DOCKER_ID, HUB_ID):
        archived = _update(tools, target.value, status="archived")
        assert not archived.is_error, archived.structured_content
        assert _read(tools, target.value)["status"] == "archived"

    view = _read(tools, note_id)
    assert (view["body"], view["hub"], view["related"]) == ("See [[docker-conventions]].", HUB_ID.value, [DOCKER_ID.value])
    updated = _update(tools, note_id, body="Still see [[docker-conventions]].")
    assert not updated.is_error, updated.structured_content
