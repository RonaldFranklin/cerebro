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


def test_preview_and_approval_check_the_live_base_again() -> None:
    from dataclasses import replace

    from tests.unit.application.write_kit import SSH

    kit = Kit()
    pid = kit.submit_update()
    kit.validate(pid)
    kit.curate(pid)
    listing = ListProposals(kit.proposals, kit.world.notes, kit.clock)
    request = ListProposalsRequest(proposal_id=pid)
    original = listing.execute(request, owner_ctx()).preview
    assert original
    assert original.base
    assert original.base_label == "base"
    assert not original.stale_base
    assert original.validation == kit.stored(pid).validation
    reviewed = kit.stored(pid)
    base = kit.world.notes.get(SSH)
    assert base
    kit.world.notes._notes[SSH] = replace(base, body="Changed since preview.\n")
    with pytest.raises(ProposalConflict):
        kit.review(pid, expected_revision=reviewed.revision, require_fresh_base=True)
    assert kit.stored(pid) == reviewed
    current = listing.execute(request, owner_ctx()).preview
    assert current
    assert current.base
    assert current.base_label == "current"
    assert current.stale_base
    assert current.base.body == "Changed since preview.\n"
    del kit.world.notes._notes[SSH]
    missing = listing.execute(request, owner_ctx()).preview
    assert missing
    assert missing.base is None
    assert missing.base_label == "current"
    assert missing.stale_base
    with pytest.raises(ProposalConflict):
        kit.review(pid, expected_revision=reviewed.revision, require_fresh_base=True)


def test_create_has_empty_base_even_if_the_target_is_taken() -> None:
    from cerebro.domain.decision import Decision
    from tests.unit.application.write_kit import SSH

    kit = Kit()
    pid = kit.submit_create()
    kit.validate(pid)
    kit.curate(pid)
    request = ListProposalsRequest(proposal_id=pid)
    listing = ListProposals(kit.proposals, kit.world.notes, kit.clock)
    preview = listing.execute(request, owner_ctx()).preview
    assert preview
    assert preview.base is None
    assert preview.base_label == "empty"
    assert not preview.stale_base
    kit.overwrite(kit.stored(pid).override_decision(Decision.create(SSH), kit.clock.now()))
    occupied = listing.execute(request, owner_ctx()).preview
    assert occupied
    assert occupied.base is None
    assert occupied.base_label == "empty"
    assert occupied.stale_base
    with pytest.raises(ProposalConflict):
        kit.review(pid, require_fresh_base=True)


def test_resume_requires_inspected_revision_including_applied_retries() -> None:
    from cerebro.application.dto import ApplyOutcome, ProposalIdRequest

    kit = Kit()
    pid = kit.submit_create()
    kit.validate(pid)
    kit.curate(pid)
    inspected = kit.stored(pid).revision
    approved = kit.review(pid, expected_revision=inspected, require_fresh_base=True)
    assert approved.revision == kit.stored(pid).revision
    before = kit.commits
    apply = kit.apply_uc()
    with pytest.raises(ProposalConflict):
        apply.execute(ProposalIdRequest(proposal_id=pid, expected_revision=inspected), owner_ctx())
    assert kit.commits == before
    req = ProposalIdRequest(proposal_id=pid, expected_revision=approved.revision)
    assert apply.execute(req, owner_ctx()).outcome is ApplyOutcome.APPLIED
    with pytest.raises(ProposalConflict):
        apply.execute(req, owner_ctx())
    req = ProposalIdRequest(proposal_id=pid, expected_revision=kit.stored(pid).revision)
    assert apply.execute(req, owner_ctx()).outcome is ApplyOutcome.ALREADY_APPLIED
    assert kit.commits == before + 1
