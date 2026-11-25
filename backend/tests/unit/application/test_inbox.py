import pytest

from cerebro.application.dto import ListProposalsRequest, ReviewAction
from cerebro.application.errors import PermissionDenied, ProposalConflict, ProposalNotApplicable, ProposalNotFound
from cerebro.application.use_cases import ListProposals
from cerebro.domain.enums import Sensitivity
from tests.unit.application.write_kit import Kit, agent_ctx, owner_ctx


def test_metadata_and_reviewable_detail() -> None:
    kit = Kit()
    pid = kit.submit_update()
    listing = ListProposals(kit.proposals, kit.world.notes, kit.clock)
    for ctx in (agent_ctx(Sensitivity.RESTRICTED), owner_ctx(Sensitivity.INTERNAL)):
        for req in (ListProposalsRequest(), ListProposalsRequest(proposal_id=pid)):
            with pytest.raises(PermissionDenied):
                listing.execute(req, ctx)
    with pytest.raises(ProposalNotApplicable):
        listing.execute(ListProposalsRequest(proposal_id=pid), owner_ctx())
    with pytest.raises(ProposalNotFound):
        listing.execute(ListProposalsRequest(proposal_id="missing"), owner_ctx())
    kit.validate(pid)
    kit.curate(pid)
    (summary,) = listing.execute(ListProposalsRequest(), owner_ctx()).items
    assert summary.revision == kit.stored(pid).revision
    assert listing.execute(ListProposalsRequest(), owner_ctx()).preview is None
    detail = listing.execute(ListProposalsRequest(proposal_id=pid), owner_ctx()).preview
    assert detail
    assert detail.candidate
    assert detail.candidate.body == kit.stored(pid).content.body
    assert detail.decision == kit.stored(pid).decision
    with pytest.raises(ProposalConflict):
        kit.review(pid, expected_revision=summary.revision - 1)
    assert kit.stored(pid).revision == summary.revision
    kit.review(pid, ReviewAction.REJECT, reason="Declined", expected_revision=summary.revision)
    with pytest.raises(ProposalNotApplicable):
        listing.execute(ListProposalsRequest(proposal_id=pid), owner_ctx())
