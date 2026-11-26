from dataclasses import replace
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from cerebro.adapters.inbound.browser_api import browser_api
from cerebro.adapters.inbound.browser_history import install_browser_history
from cerebro.application.browser import BrowserBinding
from cerebro.application.history import MAX_DIFF_CHARS, ChangeUnavailable, HistoryRequest
from cerebro.application.ports.notes import NoteChange
from cerebro.bootstrap.browser import owner_credentials
from cerebro.bootstrap.container import build_container
from cerebro.domain.access import AccessCeiling, AccessContext
from cerebro.domain.commit_message import CommitMessage
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Sensitivity
from cerebro.domain.note import NoteId
from tests.integration.content_kit import DOCKER_ID, VAULT_ID, make_sample_repo


@pytest.mark.parametrize("listener", ["private", "public"])
def test_only_a_private_owner_can_list_and_read_an_encrypted_diff(tmp_path: Path, listener: str) -> None:
    sample = make_sample_repo(tmp_path)
    container = build_container(sample.settings)
    before = container.notes.get(VAULT_ID)
    assert before is not None
    malicious = "<script>synthetic()</script>"
    changed = replace(before, body=f"{malicious}\n[[missing|unavailable metadata]]\n" + ("large sample\n" * 3000))
    revision = container.notes.apply(
        [NoteChange(note=changed, expected_hash=content_hash(before))],
        CommitMessage.for_owner_edit(subject=f"Update note: {VAULT_ID.value}", edit_id="owner-edit-1234"),
    )
    owner_credentials(sample.settings).provision(b"history-fixture")
    origin = f"https://{listener}.example.invalid"
    # Even a deliberately wide public ceiling cannot turn a public socket into the private listener.
    binding = BrowserBinding("private" if listener == "private" else "public", origin)
    app = browser_api(container.browser_auth, binding, AccessCeiling(Sensitivity.RESTRICTED))
    install_browser_history(app, container.use_cases.history)
    with TestClient(app, base_url=origin) as client:
        endpoints = ("/api/changes", f"/api/changes/{revision}")
        for endpoint in endpoints:
            assert client.get(endpoint).status_code == 401
        assert (
            client.post(
                "/api/login", headers={"Origin": origin, "X-Cerebro-Request": "1"}, json={"password": "history-fixture"}
            ).status_code
            == 200
        )
        if listener == "public":
            for endpoint in endpoints:
                assert client.get(endpoint, headers={"X-Forwarded-Host": "private"}).status_code == 403
            return
        rows = client.get(endpoints[0]).json()["changes"]
        assert [row["revision"] for row in rows] == [revision]
        assert rows[0]["actor"] == "owner"
        detail = client.get(endpoints[1])
        assert detail.status_code == 200
        assert detail.headers["cache-control"] == "no-store"
        payload = detail.json()
        assert malicious in payload["diff"]
        assert "missing" not in payload["diff"]
        assert "unavailable metadata" not in payload["diff"]
        assert payload["truncated"]
        assert len(payload["diff"]) <= MAX_DIFF_CHARS
        assert payload["operation"] == "update"
        for invalid in ("HEAD", "HEAD~1", "--all", "f" * 40, revision[:12]):
            assert client.get(f"/api/changes/{invalid}").status_code == 404
        clean = container.notes.get(DOCKER_ID)
        assert clean
        readable = container.notes.apply(
            [
                NoteChange(
                    note=replace(clean, body=f"See [[{VAULT_ID.value}|alias]]."), expected_hash=content_hash(clean)
                )
            ],
            CommitMessage.for_owner_edit(subject=f"Update note: {DOCKER_ID.value}", edit_id="owner-edit-5678"),
        )
        (sample.root / "notes" / f"{VAULT_ID.value}.md").write_bytes(b"invalid note")
        assert client.get(endpoints[1]).json() == {"error": "Change unavailable."}
        assert client.get(endpoints[1]).status_code == 404
        clean_detail = client.get(f"/api/changes/{readable}")
        assert clean_detail.status_code == 200
        assert VAULT_ID.value not in clean_detail.json()["diff"]
        assert [row["revision"] for row in client.get(endpoints[0]).json()["changes"]] == [readable]
        (sample.root / "notes" / f"{VAULT_ID.value}.md").unlink()
        assert client.get(endpoints[1]).status_code == 404
        assert [row["revision"] for row in client.get(endpoints[0]).json()["changes"]] == [readable]


def test_legacy_create_identifies_its_agent_and_corrupt_history_fails_closed(tmp_path: Path) -> None:
    sample = make_sample_repo(tmp_path)
    container = build_container(sample.settings)
    base = container.notes.get(VAULT_ID)
    assert base
    created = replace(base, id=NoteId("legacy-record"))
    message = CommitMessage(
        subject="Add note: legacy-record",
        proposal_id="legacy-id",
        proposed_by="claude-code",
        curated_by="curator/null",
        approved_by="owner",
    )
    revision = container.notes.apply([NoteChange(note=created)], message)
    owner = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))
    history = container.use_cases.history
    detail = history.detail(HistoryRequest(revision), owner)
    assert detail.change.actor == "claude-code"
    assert detail.change.operation == "create"
    assert base.body.rstrip().splitlines()[0] in detail.diff
    # A malformed historical parent is never treated as a newly created note, even when the latest file is readable.
    path = f"notes/{created.id.value}.md"
    stored = (sample.root / path).read_bytes()
    (sample.root / path).write_bytes(b"invalid format")
    git = sample.git()
    git.commit("Manual repair in progress", [path])
    (sample.root / path).write_bytes(stored)
    repaired = git.commit(
        CommitMessage.for_owner_edit(subject="Update note: legacy-record", edit_id="owner-edit-01").render(), [path]
    )
    assert repaired not in {row.revision for row in history.list(owner)}
    with pytest.raises(ChangeUnavailable):
        history.detail(HistoryRequest(repaired), owner)
