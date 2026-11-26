from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from cerebro.adapters.inbound.browser_api import browser_api
from cerebro.adapters.inbound.browser_inbox import BrowserInbox, install_browser_inbox, install_browser_write_errors
from cerebro.application.browser import BrowserBinding
from cerebro.application.dto import OwnerEditNoteRequest, ReviewAction, ReviewProposalRequest, SubmitProposalRequest
from cerebro.bootstrap.browser import owner_credentials
from cerebro.bootstrap.container import Container, build_container
from cerebro.domain.access import AccessCeiling
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.proposal import ProposalKind, ProposedContent
from cerebro.domain.vocabulary import Area, NoteType
from tests.integration.content_kit import HUB_ID, ISLAND_ID, make_sample_repo
from tests.integration.test_git import raw_git
from tests.unit.application.write_kit import agent_ctx, owner_ctx


@dataclass
class Service:
    container: Container
    private: TestClient
    public: TestClient

    def submit(self, *, create: bool = False) -> str:
        base = self.container.notes.get(ISLAND_ID)
        assert base
        candidate = ProposedContent(
            body="Updated owner-reviewed fixture.\n",
            sensitivity=Sensitivity.RESTRICTED,
            title="Browser example" if create else None,
            summary="This is a synthetic note for exercising the private browser review path." if create else None,
            area=Area("infrastructure") if create else None,
            type=NoteType("guide") if create else None,
            hub=HUB_ID if create else None,
            priority=Priority.P2 if create else None,
        )
        result = self.container.use_cases.submit_proposal.execute(
            SubmitProposalRequest(
                kind=ProposalKind.CREATE if create else ProposalKind.UPDATE,
                target=None if create else ISLAND_ID,
                base_hash=None if create else content_hash(base),
                content=candidate,
            ),
            replace(agent_ctx(Sensitivity.RESTRICTED), token_id="fixture-token"),
        )
        assert result.valid
        return f"/api/inbox/{result.proposal_id}"


@pytest.fixture
def service(tmp_path: Path) -> Iterator[Service]:
    fixture = make_sample_repo(tmp_path)
    container = build_container(fixture.settings)
    container.start()
    owner_credentials(fixture.settings).provision(b"fixture")
    cases = container.use_cases
    inbox = BrowserInbox(cases.list_proposals, cases.amend_proposal, cases.review_proposal, cases.apply_proposal)
    with ExitStack() as stack:
        clients = []
        for name, level in (("private", Sensitivity.RESTRICTED), ("public", Sensitivity.INTERNAL)):
            origin = f"https://{name}.example.invalid"
            app = browser_api(container.browser_auth, BrowserBinding(name, origin), AccessCeiling(level))
            install_browser_write_errors(app)
            install_browser_inbox(app, inbox)
            client = stack.enter_context(TestClient(app, base_url=origin))
            login = client.post(
                "/api/login", headers={"Origin": origin, "X-Cerebro-Request": "1"}, json={"password": "fixture"}
            )
            client.headers.update({"Origin": origin, "X-CSRF-Token": login.json()["csrf_token"]})
            clients.append(client)
        yield Service(container, *clients)


def test_update_diff_approval_commit_and_resume(service: Service) -> None:
    path = service.submit()
    client = service.private
    (item,) = client.get("/api/inbox").json()["items"]
    assert "candidate" not in item
    preview = client.get(path).json()
    assert preview["revision"] == item["revision"]
    assert preview["base_label"] == "base"
    assert preview["stale_base"] is False
    assert preview["validation"]["ok"]
    assert preview["candidate"]["body"] in preview["diff"]["body"]
    assert "sensitivity" in preview["diff"]["fields"]
    response = client.post(path + "/approve", json={"revision": item["revision"]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["commit"] == service.container.notes.head()
    assert result["warnings"][0]["code"] == "previous_version_in_history"
    log = raw_git(service.container.settings.content_repo, "log", "-1", "--format=%B")
    for trailer in (
        "Proposed-by: claude-code",
        "Curated-by: curator/policy",
        "Approved-by: owner",
        f"Proposal-Id: {item['id']}",
    ):
        assert trailer in log
    (current,) = client.get("/api/inbox").json()["items"]
    assert client.post(path + "/resume", json={"revision": item["revision"]}).status_code == 409
    resumed = client.post(path + "/resume", json={"revision": current["revision"]})
    assert resumed.json()["outcome"] == "already_applied"
    assert service.container.notes.head() == result["commit"]


def test_stale_preview_conflicts_before_approval(service: Service) -> None:
    path = service.submit()
    client = service.private
    original = client.get(path).json()
    source = service.container.notes.get(ISLAND_ID)
    assert source
    service.container.use_cases.owner_edit_note.execute(
        OwnerEditNoteRequest(note=replace(source, body="Concurrent replacement."), expected_hash=content_hash(source)),
        owner_ctx(),
    )
    head = service.container.notes.head()
    refreshed = client.get(path).json()
    assert refreshed["base_label"] == "current"
    assert refreshed["stale_base"] is True
    assert refreshed["base"]["body"] == "Concurrent replacement."
    assert client.post(path + "/approve", json={"revision": original["revision"]}).status_code == 409
    assert service.container.notes.head() == head
    assert client.get(path).json()["revision"] == original["revision"]


def test_create_amend_validation_rejection_and_rejected_payload(service: Service) -> None:
    path = service.submit(create=True)
    client = service.private
    preview = client.get(path).json()
    assert preview["base_label"] == "empty"
    assert preview["base"] is None
    rev = preview["revision"]
    failure = client.post(path + "/amend", json={"revision": rev, "body": ""})
    assert failure.status_code == 422
    amended = client.post(path + "/amend", json={"revision": rev, "body": "Owner-amended text."})
    assert amended.status_code == 200, amended.text
    assert amended.json()["revision"] > rev
    assert client.post(path + "/reject", json={"revision": rev, "reason": "Declined."}).status_code == 409
    head = service.container.notes.head()
    refused = client.post(path + "/reject", json={"revision": amended.json()["revision"], "reason": "Declined."})
    assert refused.json()["status"] == "rejected"
    assert client.get(path).status_code == 409
    assert "Owner-amended" not in client.get("/api/inbox").text
    assert service.container.notes.head() == head


def test_resume_interrupted_approval(service: Service) -> None:
    path = service.submit()
    client = service.private
    preview = client.get(path).json()
    service.container.use_cases.review_proposal.execute(
        ReviewProposalRequest(
            proposal_id=preview["id"], action=ReviewAction.APPROVE, expected_revision=preview["revision"]
        ),
        owner_ctx(),
    )
    (approved,) = client.get("/api/inbox").json()["items"]
    resumed = client.post(path + "/resume", json={"revision": approved["revision"]})
    assert resumed.json()["outcome"] == "applied"


def test_public_inbox_denial_and_input_boundaries(service: Service) -> None:
    path = service.submit()
    assert service.public.get("/api/inbox").status_code == 403
    assert service.public.get(path).status_code == 403
    for action in ("amend", "approve", "resume", "reject"):
        assert service.public.post(path + "/" + action, json={}).status_code == 403
    client = service.private
    rev = client.get(path).json()["revision"]
    assert client.post(path + "/approve", json={"revision": rev}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post(path + "/approve", json={"revision": True}).status_code == 422
    assert client.post(path + "/approve", json={"revision": rev, "extra": "unexpected"}).status_code == 422
    assert (
        client.post(
            path + "/approve", content='{"revision":1,"revision":2}', headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )
    assert client.post(path + "/approve?secret=1", json={"revision": rev}).status_code == 422
    assert client.post(path + "/approve", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert (
        client.post(path + "/approve", content=b"x" * 65537, headers={"Content-Type": "application/json"}).status_code
        == 413
    )


def test_create_uses_preview_target(service: Service) -> None:
    path = service.submit(create=True)
    preview = service.private.get(path).json()
    response = service.private.post(path + "/approve", json={"revision": preview["revision"]})
    assert response.status_code == 200, response.text
    assert response.json()["note_id"] == preview["candidate"]["id"]
    assert response.json()["commit"] == service.container.notes.head()
