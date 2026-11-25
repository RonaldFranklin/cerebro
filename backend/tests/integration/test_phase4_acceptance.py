"""Exercise Phase 4 from authenticated SDK submission through owner approval and its git commit."""

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx2
from devtools.mcp_sample import create_sample
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from cerebro.bootstrap.container import build_container
from cerebro.bootstrap.settings import load_settings
from cerebro.domain.proposal import ProposalStatus
from tests.integration.test_cli_bootstrap import invoke
from tests.integration.test_git import raw_git
from tests.integration.test_mcp_propose import CREATE, PROPOSE
from tests.integration.test_mcp_transports import free_port, serving


def test_proposal_admission_review_and_commit(tmp_path: Path) -> None:
    config = create_sample(tmp_path / "instance")
    with config.open("a", encoding="utf-8") as file:
        file.write("proposals_per_hour = 2\nproposals_pending = 1\n")
    settings = load_settings(config, {})
    app = build_container(settings)
    owner = ("--config", str(config))
    issue = invoke(*owner, "token", "issue", "sdk-author", "--scope", "propose", "--ceiling", "internal")
    assert issue.returncode == 0, issue.stderr
    port = free_port()
    initial = raw_git(settings.content_repo, "rev-parse", "HEAD").strip()
    marker = "synthetic-sample-to-discard"
    fake_key = "-----BEGIN " + "PRIVATE KEY-----\n" + marker + "\n-----END PRIVATE KEY-----"

    async def exercise(client: Client, monitor: sqlite3.Connection) -> None:
        async def check_limit() -> None:
            version = monitor.execute("PRAGMA data_version").fetchone()
            head = app.notes.head()
            refused = await client.call_tool(PROPOSE, CREATE)
            assert refused.is_error
            assert [item.model_dump()["text"] for item in refused.content] == ["Proposal limit reached."]
            assert monitor.execute("PRAGMA data_version").fetchone() == version
            assert app.notes.head() == head

        assert [tool.name for tool in (await client.list_tools()).tools] == [PROPOSE]
        reply = await client.call_tool(PROPOSE, CREATE)
        assert reply.structured_content
        pid = reply.structured_content["proposal_id"]
        assert reply.structured_content == {"proposal_id": pid, "valid": True}
        before = app.proposals.get(pid)
        assert before
        assert before.validation
        assert before.validation.ok
        assert before.status is ProposalStatus.NEEDS_OWNER
        assert before.curated_by == "curator/null"
        assert before.approved_by is None
        assert app.notes.head() == initial
        await check_limit()  # one pending, one of two hourly slots used

        preview = invoke(*owner, "inbox", "list", "--id", pid)
        assert preview.returncode == 0, preview.stderr
        detail = json.loads(preview.stdout)
        assert detail["candidate"]["body"] == CREATE["body"]
        assert detail["decision"]["kind"] == "needs_owner"
        approved = invoke(*owner, "inbox", "approve", pid, "--revision", str(detail["revision"]))
        assert approved.returncode == 0, approved.stderr
        applied = json.loads(approved.stdout)
        assert applied["status"] == "applied"
        assert applied["commit"] == app.notes.head()
        assert raw_git(settings.content_repo, "rev-list", "--count", f"{initial}..HEAD").strip() == "1"
        assert raw_git(settings.content_repo, "log", "-1", "--format=%B").strip().splitlines()[-4:] == [
            "Proposed-by: sdk-author",
            "Curated-by: curator/null",
            "Approved-by: owner",
            f"Proposal-Id: {pid}",
        ]
        assert str(CREATE["body"]) in raw_git(settings.content_repo, "show", "HEAD:notes/workspace-guidelines.md")

        # Preserve the exact first inserted row image, even if the service immediately updates it.
        monitor.executescript("""
            CREATE TABLE observed_insert (id TEXT, status TEXT, payload BLOB);
            CREATE TRIGGER capture_proposal AFTER INSERT ON proposals BEGIN
                INSERT INTO observed_insert VALUES (NEW.id, NEW.status, NEW.payload);
            END;
        """)
        rejected = await client.call_tool(PROPOSE, {**CREATE, "body": fake_key})
        assert rejected.structured_content
        rejected_id = rejected.structured_content["proposal_id"]
        assert rejected.structured_content == {"proposal_id": rejected_id, "valid": False}
        [(first_id, first_status, first_payload)] = monitor.execute("SELECT * FROM observed_insert").fetchall()
        assert first_id == rejected_id
        assert first_status == "rejected"
        assert marker.encode() not in app.cipher.decrypt(first_payload)
        row = app.proposals.get(rejected_id)
        assert row
        assert row.status is ProposalStatus.REJECTED
        assert row.validation
        assert any(v.code.value == "secret_detected" for v in row.validation.violations)
        assert row.content.body == ""
        assert marker not in repr(row)
        assert marker not in rejected.model_dump_json()
        await check_limit()  # zero pending, both hourly slots used
        assert len(app.proposals.find()) == 2
        assert app.notes.head() == applied["commit"]

    async def run() -> None:
        async with (
            asyncio.timeout(30),
            httpx2.AsyncClient(headers={"Authorization": "Bearer " + issue.stdout.strip()}, trust_env=False) as http,
            Client(streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http)) as client,
        ):
            with closing(sqlite3.connect(settings.state_dir / "state.sqlite")) as monitor:
                await exercise(client, monitor)

    with serving(config, port):
        asyncio.run(run())
    assert marker not in config.with_suffix(".log").read_text()
