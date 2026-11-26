import pytest

from cerebro.application.dto import ListProposalsRequest, ReviewAction
from cerebro.application.errors import PermissionDenied, ProposalConflict, ProposalNotApplicable, ProposalNotFound
from cerebro.application.use_cases import ListProposals
from cerebro.domain.enums import Sensitivity
from tests.unit.application.write_kit import Kit, agent_ctx, owner_ctx


def test_metadata_and_reviewable_detail() -> None:
    kit = Kit()
    pid = kit.submit_update()
    listing = ListProposals(kit.proposals, kit.world.notes, kit.validate_uc())
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
    listing = ListProposals(kit.proposals, kit.world.notes, kit.validate_uc())
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
    assert current.validation is None
    assert current.historical_validation == reviewed.validation
    assert current.base.body == "Changed since preview.\n"
    del kit.world.notes._notes[SSH]
    missing = listing.execute(request, owner_ctx()).preview
    assert missing
    assert missing.base is None
    assert missing.base_label == "current"
    assert missing.stale_base
    assert missing.historical_validation == reviewed.validation
    assert missing.validation
    assert not missing.validation.ok
    assert missing.validation.errors[0].field == "target"
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
    listing = ListProposals(kit.proposals, kit.world.notes, kit.validate_uc())
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


@pytest.mark.parametrize("create", [True, False])
def test_refresh_approval_and_resume_preserve_the_previewed_dates(create: bool) -> None:
    from datetime import timedelta

    from cerebro.domain.content_hash import content_hash

    kit = Kit()
    pid = kit.submit_create() if create else kit.submit_update()
    kit.validate(pid)
    kit.curate(pid)
    listing = ListProposals(kit.proposals, kit.world.notes, kit.validate_uc())
    request = ListProposalsRequest(proposal_id=pid)
    first = listing.execute(request, owner_ctx()).preview
    assert first
    assert first.candidate
    kit.clock.advance(timedelta(days=1))
    refreshed = listing.execute(request, owner_ctx()).preview
    assert refreshed == first
    kit.review(pid, require_fresh_base=True)
    kit.clock.advance(timedelta(days=2))
    kit.apply(pid)
    stored = kit.world.notes.get(first.candidate.id)
    assert stored
    assert content_hash(stored) == first.candidate.content_hash
    assert (stored.created, stored.updated) == (first.candidate.created, first.candidate.updated)


def test_preview_rechecks_validation_when_a_referenced_note_disappears() -> None:
    from cerebro.domain.note import NoteId
    from cerebro.domain.report import ViolationCode

    kit = Kit()
    pid = kit.submit_update()
    kit.validate(pid)
    kit.curate(pid)
    del kit.world.notes._notes[NoteId("infrastructure")]
    preview = (
        ListProposals(kit.proposals, kit.world.notes, kit.validate_uc())
        .execute(ListProposalsRequest(proposal_id=pid), owner_ctx())
        .preview
    )
    assert preview
    assert not preview.stale_base
    assert preview.historical_validation
    assert preview.historical_validation.ok
    assert preview.validation
    assert ViolationCode.HUB_NOT_FOUND in preview.validation.codes


def test_preview_detects_duplicate_added_after_submission() -> None:
    from dataclasses import replace

    from cerebro.application.dto import OwnerEditNoteRequest
    from cerebro.domain.content_hash import content_hash
    from cerebro.domain.report import ViolationCode
    from tests.unit.application.write_kit import SSH

    kit = Kit()
    title = "Gardening calendar"
    summary = "Track these planting dates for seedlings and flowering bulbs during spring."
    pid = kit.submit_create(title=title, summary=summary)
    kit.validate(pid)
    kit.curate(pid)
    current = kit.world.notes.get(SSH)
    assert current
    kit.edit_uc().execute(
        OwnerEditNoteRequest(note=replace(current, title=title, summary=summary), expected_hash=content_hash(current)),
        owner_ctx(),
    )
    listing = ListProposals(kit.proposals, kit.world.notes, kit.validate_uc())
    preview = listing.execute(ListProposalsRequest(proposal_id=pid), owner_ctx()).preview
    assert preview
    assert preview.historical_validation
    assert ViolationCode.POSSIBLE_DUPLICATE not in preview.historical_validation.codes
    assert preview.validation
    assert ViolationCode.POSSIBLE_DUPLICATE in preview.validation.codes
