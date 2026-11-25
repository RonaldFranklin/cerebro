import json
from dataclasses import replace
from pathlib import Path

import pytest

from cerebro.application.dto import (
    OwnerEditNoteRequest,
    ReviewAction,
    ReviewProposalRequest,
    SubmitProposalRequest,
)
from cerebro.bootstrap.container import build_container
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Sensitivity
from cerebro.domain.proposal import ProposalKind, ProposedContent
from tests.integration.content_kit import ISLAND_ID, make_sample_repo
from tests.integration.test_cli_bootstrap import SAMPLE, invoke
from tests.unit.application.write_kit import agent_ctx, owner_ctx


@pytest.mark.parametrize("action", ["approve", "reject", "resume", "conflict", "amend"])
def test_preview_review_and_retry(tmp_path: Path, action: str) -> None:
    sample = make_sample_repo(tmp_path)
    c = build_container(sample.settings)
    c.start()
    config = tmp_path / "dev.toml"
    config.write_text(SAMPLE.replace("keys/fernet.key", "secrets/cerebro.key"))
    args = ("--config", str(config), "inbox")
    source = c.notes.get(ISLAND_ID)
    assert source
    change = "# Updated\n\nReview this replacement.\n"
    submitted = c.use_cases.submit_proposal.execute(
        SubmitProposalRequest(
            kind=ProposalKind.UPDATE,
            target=ISLAND_ID,
            base_hash=content_hash(source),
            content=ProposedContent(body=change, sensitivity=Sensitivity.RESTRICTED),
        ),
        replace(agent_ctx(Sensitivity.RESTRICTED), token_id="test-token"),
    )
    pid = submitted.proposal_id
    assert submitted.valid
    (item,) = json.loads(invoke(*args, "list").stdout)
    assert item["id"] == pid
    assert "body" not in item
    detail = json.loads(invoke(*args, "list", "--id", pid).stdout)
    assert detail["candidate"]["body"] == change
    assert detail["decision"]["kind"] == "needs_owner"
    assert invoke(*args, "approve", pid, "--revision", "999").returncode == 1
    revision = detail["revision"]
    if action == "amend":
        body = "Replace with the owner-reviewed text."
        edit = invoke(*args, "amend", pid, "--revision", str(revision), stdin=body)
        assert edit.returncode == 0, edit.stderr
        preview = json.loads(invoke(*args, "list", "--id", pid).stdout)
        assert preview["candidate"]["body"] == body
        assert invoke(*args, "approve", pid, "--revision", str(revision)).returncode == 1
        assert invoke(*args, "approve", pid, "--revision", str(preview["revision"])).returncode == 0
        saved = c.notes.get(ISLAND_ID)
        assert saved
        assert saved.body == body
        row = c.proposals.get(pid)
        assert row
        assert row.source_agent == "claude-code"
        assert row.curated_by == "curator/policy"
        return
    if action == "reject":
        head = c.notes.head()
        rejected = invoke(*args, "reject", pid, "--revision", str(revision), "--reason", "Reviewed and declined.")
        assert rejected.returncode == 0
        assert json.loads(rejected.stdout)["status"] == "rejected"
        assert c.notes.head() == head
        assert invoke(*args, "approve", pid, "--resume").returncode == 1
        return
    if action == "resume":
        c.use_cases.review_proposal.execute(
            ReviewProposalRequest(proposal_id=pid, action=ReviewAction.APPROVE, expected_revision=revision), owner_ctx()
        )
        applied = invoke(*args, "approve", pid, "--resume")
        assert applied.returncode == 0
        assert json.loads(applied.stdout)["outcome"] == "applied"
        return
    if action == "conflict":
        c.use_cases.owner_edit_note.execute(
            OwnerEditNoteRequest(note=replace(source, body="Another change."), expected_hash=content_hash(source)),
            owner_ctx(),
        )
        head = c.notes.head()
        failed = invoke(*args, "approve", pid, "--revision", str(revision))
        assert failed.returncode == 1
        assert json.loads(failed.stdout)["outcome"] == "conflict"
        assert c.notes.head() == head
        return
    approved = invoke(*args, "approve", pid, "--revision", str(detail["revision"]), "--merge-into", ISLAND_ID.value)
    assert approved.returncode == 0, approved.stderr
    assert json.loads(approved.stdout)["status"] == "applied"
    assert json.loads(approved.stdout)["warnings"][0]["code"] == "previous_version_in_history"
    head = c.notes.head()
    assert json.loads(invoke(*args, "approve", pid, "--resume").stdout)["outcome"] == "already_applied"
    assert c.notes.head() == head


@pytest.mark.parametrize(
    "args",
    [
        ("list", "--id", "a" * 65),
        ("approve", "a", "--revision", "-1"),
        ("approve", "a", "--resume", "--create", "b"),
        ("reject", "a", "--revision", "0", "--reason", "secret" * 700),
    ],
)
def test_invalid_inputs_fail_before_wiring(args: tuple[str, ...]) -> None:
    bad = invoke("inbox", *args)
    assert bad.returncode == 2
    assert bad.stderr == "cerebro: invalid arguments (see --help)\n"
