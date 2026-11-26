from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from cerebro.adapters.inbound.browser_api import browser_api
from cerebro.adapters.inbound.browser_inbox import install_browser_inbox, install_browser_write_errors
from cerebro.application.browser import BrowserBinding
from cerebro.application.dto import (
    ReviewAction,
    ReviewProposalRequest,
    SubmitProposalRequest,
)
from cerebro.bootstrap.browser import owner_credentials
from cerebro.bootstrap.container import Container, build_container
from cerebro.domain.access import AccessCeiling
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Sensitivity
from cerebro.domain.proposal import ProposalKind, ProposedContent
from tests.integration.content_kit import ISLAND_ID, make_sample_repo
from tests.unit.application.write_kit import agent_ctx, owner_ctx


@dataclass
class Service:
    container: Container
    private: TestClient
    public: TestClient

    def submit(self) -> str:
        base = self.container.notes.get(ISLAND_ID)
        assert base
        candidate = ProposedContent(
            body="Updated owner-reviewed fixture.\n",
            sensitivity=Sensitivity.RESTRICTED,
        )
        result = self.container.use_cases.submit_proposal.execute(
            SubmitProposalRequest(
                kind=ProposalKind.UPDATE,
                target=ISLAND_ID,
                base_hash=content_hash(base),
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
    with ExitStack() as stack:
        clients = []
        for name, level in (("private", Sensitivity.RESTRICTED), ("public", Sensitivity.INTERNAL)):
            origin = f"https://{name}.example.invalid"
            app = browser_api(container.browser_auth, BrowserBinding(name, origin), AccessCeiling(level))
            install_browser_write_errors(app)
            install_browser_inbox(app, cases.list_proposals)
            client = stack.enter_context(TestClient(app, base_url=origin))
            login = client.post(
                "/api/login", headers={"Origin": origin, "X-Cerebro-Request": "1"}, json={"password": "fixture"}
            )
            client.headers.update({"Origin": origin, "X-CSRF-Token": login.json()["csrf_token"]})
            clients.append(client)
        yield Service(container, *clients)


def test_legacy_preview_remains_private_and_read_only(service: Service) -> None:
    path = service.submit()
    (item,) = service.private.get("/api/inbox").json()["items"]
    assert "candidate" not in item
    preview = service.private.get(path).json()
    assert preview["revision"] == item["revision"]
    assert preview["base_label"] == "base"
    assert preview["validation"]["ok"]
    assert preview["candidate"]["body"] in preview["diff"]["body"]
    assert "sensitivity" in preview["diff"]["fields"]
    assert service.public.get("/api/inbox").status_code == 403
    assert service.public.get(path).status_code == 403


@pytest.mark.parametrize("approved", [False, True], ids=["awaiting-owner", "approved-before-upgrade"])
def test_legacy_mutations_leave_rows_and_git_untouched(service: Service, approved: bool) -> None:
    path = service.submit()
    item = service.private.get(path).json()
    pid = item["id"]
    if approved:
        service.container.use_cases.review_proposal.execute(
            ReviewProposalRequest(proposal_id=pid, action=ReviewAction.APPROVE, expected_revision=item["revision"]),
            owner_ctx(),
        )
    before = service.container.proposals.get(pid)
    assert before is not None
    assert before.status.value == ("approved" if approved else "needs_owner")
    head = service.container.notes.head()

    for action, extra in (
        ("approve", {}),
        ("resume", {}),
        ("amend", {"body": "Amended."}),
        ("reject", {"reason": "Declined."}),
    ):
        payload = {"revision": before.revision, **extra}
        assert service.public.post(f"{path}/{action}", json=payload).status_code == 403
        assert service.private.post(f"{path}/{action}", json=payload).status_code == 404
        assert service.container.proposals.get(pid) == before
        assert service.container.notes.head() == head
