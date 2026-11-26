"""Exercise immediate agent writes from the installed HTTP service and stdio bridge."""

import asyncio
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx2
from devtools.mcp_sample import create_sample
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from cerebro.bootstrap.settings import load_settings
from tests.integration.test_cli_bootstrap import invoke
from tests.integration.test_git import raw_git
from tests.integration.test_mcp_transports import BRIDGE, call, free_port, post, serving

_WRITE = "cerebro_write"
_GUIDE = "cerebro_write_guide"


def test_sdk_create_update_and_audit_across_transports(tmp_path: Path) -> None:
    config = create_sample(tmp_path / "instance")
    with config.open("a", encoding="utf-8") as file:
        file.write("writes_per_hour = 3\n")
    settings = load_settings(config, {})
    port = free_port()
    initial = raw_git(settings.content_repo, "rev-parse", "HEAD").strip()

    def issue(agent: str) -> str:
        result = invoke(
            "--config",
            str(config),
            "token",
            "issue",
            agent,
            "--scope",
            "read",
            "--scope",
            "write",
            "--ceiling",
            "internal",
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    author, editor = issue("sdk-author"), issue("sdk-editor")
    marker = "synthetic-sample-to-discard"
    fake_key = "-----BEGIN " + "PRIVATE KEY-----\n" + marker + "\n-----END PRIVATE KEY-----"
    create: dict[str, object] = {
        "operation": "create",
        "request_id": "sdk-create-one",
        "body": "Use a fresh project directory and confirm the status before resuming.",
        "frontmatter": {
            "title": "Resuming a sample project",
            "summary": "How to resume work safely in a fresh directory for a sample project.",
            "priority": "P2",
            "area": "infrastructure",
            "type": "guide",
            "sensitivity": "internal",
            "hub": "infrastructure",
            "related": ["docker-conventions"],
            "review_by": "2030-06-12",
        },
    }

    async def run() -> None:
        params = StdioServerParameters(
            command=BRIDGE, args=["--private-port", str(port)], env={"CEREBRO_MCP_TOKEN": editor}
        )
        async with (
            asyncio.timeout(40),
            httpx2.AsyncClient(headers={"Authorization": "Bearer " + author}, trust_env=False) as http,
            Client(streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http)) as direct,
            Client(params, mode="legacy", read_timeout_seconds=5) as bridge,
        ):
            for client in (direct, bridge):
                names = {tool.name for tool in (await client.list_tools()).tools}
                assert names == {
                    "cerebro_overview",
                    "cerebro_search",
                    "cerebro_read",
                    "cerebro_neighbors",
                    _WRITE,
                    _GUIDE,
                }
                guide = await client.call_tool(_GUIDE, {})
                assert guide.structured_content
                assert "base_hash" in guide.structured_content["guide"]

            found = await direct.call_tool("cerebro_search", {"query": "resuming"})
            assert found.structured_content == {"results": []}
            receipt = await direct.call_tool(_WRITE, create)
            assert not receipt.is_error
            assert receipt.structured_content
            note_id = receipt.structured_content["note_id"]
            assert re.fullmatch(r"resuming-a-sample-project-[a-z0-9]{4}", note_id)
            assert receipt.structured_content["revision"] == raw_git(settings.content_repo, "rev-parse", "HEAD").strip()
            assert "Written-by: sdk-author" in raw_git(settings.content_repo, "log", "-1", "--format=%B").splitlines()
            replay = await direct.call_tool(_WRITE, create)
            assert replay.structured_content
            assert replay.structured_content["revision"] == receipt.structured_content["revision"]

            read = await bridge.call_tool("cerebro_read", {"id": note_id})
            assert read.structured_content
            assert read.structured_content["body"] == create["body"]
            writable = (
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
            metadata = {field: read.structured_content[field] for field in writable}
            assert metadata["related"] == ["docker-conventions"]
            assert metadata["review_by"] == "2030-06-12"
            update = {
                "operation": "update",
                "target": note_id,
                "base_hash": read.structured_content["base_hash"],
                "frontmatter": metadata,
                "body": "Check the latest revision before resuming work.",
            }
            amended = await bridge.call_tool(_WRITE, update)
            assert not amended.is_error
            assert amended.structured_content
            expected_head = amended.structured_content["revision"]
            assert expected_head == raw_git(settings.content_repo, "rev-parse", "HEAD").strip()
            assert "Written-by: sdk-editor" in raw_git(settings.content_repo, "log", "-1", "--format=%B").splitlines()
            saved = (await bridge.call_tool("cerebro_read", {"id": note_id})).structured_content
            assert saved
            assert {field: saved[field] for field in writable} == metadata
            stale = await bridge.call_tool(_WRITE, update)
            assert stale.is_error
            assert stale.structured_content
            assert stale.structured_content["findings"][0]["code"] == "stale_base"

            # Both transports preserve extra keys and explicit nulls through authenticated admission.
            typo = await bridge.call_tool(_WRITE, {**update, "request_id": None})
            assert typo.structured_content
            assert typo.structured_content["findings"][0]["code"] == "unknown_field"
            typo = await bridge.call_tool(_WRITE, update)
            assert typo.structured_content
            assert typo.structured_content["findings"][0]["code"] == "rate_limited"

            rejected = await direct.call_tool(_WRITE, {**create, "request_id": "discard", "body": fake_key})
            assert rejected.is_error
            assert rejected.structured_content
            assert rejected.structured_content["findings"][0]["code"] == "secret_detected"
            assert marker not in rejected.model_dump_json()
            refused = await direct.call_tool(_WRITE, {**create, "request_id": "another"})
            assert refused.is_error
            assert refused.structured_content
            assert refused.structured_content["findings"][0]["code"] == "rate_limited"
            assert expected_head == raw_git(settings.content_repo, "rev-parse", "HEAD").strip()
            assert raw_git(settings.content_repo, "rev-list", "--count", f"{initial}..HEAD").strip() == "2"

    with serving(config, port):
        malformed = issue("sdk-malformed")
        for extra in ({"author": "injected"}, {"target": None}, {"base_hash": None}):
            status, body = post(port, malformed, call(_WRITE, {**create, **extra}))
            assert status == 200
            finding = json.loads(body)["result"]["structuredContent"]["findings"][0]
            assert finding["code"] == "unknown_field"
            assert finding["field"] == "request"
            assert "injected" not in finding["hint"]
        status, body = post(port, malformed, call(_WRITE, create))
        assert status == 200
        assert json.loads(body)["result"]["structuredContent"]["findings"][0]["code"] == "rate_limited"
        assert initial == raw_git(settings.content_repo, "rev-parse", "HEAD").strip()
        asyncio.run(run())
    with closing(sqlite3.connect(settings.state_dir / "state.sqlite")) as db:
        assert db.execute("SELECT count(*) FROM write_attempts").fetchone() == (9,)
    log = config.with_suffix(".log").read_text()
    for sensitive in (marker, author, editor, create["body"]):
        assert str(sensitive) not in log
