from dataclasses import fields, replace

import pytest

from cerebro.application.dto import AmendProposalRequest, ReviewAction, SubmitProposalRequest
from cerebro.application.errors import (
    AmendmentValidationError,
    PermissionDenied,
    ProposalConflict,
)
from cerebro.application.use_cases import AdmitProposal, AmendProposal
from cerebro.domain.content_hash import content_hash
from cerebro.domain.decision import Decision
from cerebro.domain.enums import Sensitivity
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.note import NoteId
from cerebro.domain.proposal import Proposal, ProposalKind, ProposalLimits, ProposalStatus, ProposedContent
from cerebro.domain.report import ViolationCode
from tests.fakes.stores import FakeCurator
from tests.unit.application.write_kit import Kit, agent_ctx, new_content, owner_ctx

AGENT = replace(agent_ctx(), token_id="token-123")


def intake(k: Kit) -> AdmitProposal:
    return AdmitProposal(k.submit_uc(), k.validate_uc(), k.curate_uc(), k.store, k.clock, ProposalLimits())


@pytest.mark.parametrize(
    ("body", "ok"), [("A clean note.", True), ("marked invalid", False), ("-----BEGIN PRIVATE KEY-----", False)]
)
def test_first_durable_row_is_reviewable_or_scrubbed(body: str, ok: bool) -> None:
    k = Kit()
    before = k.commits
    observed: list[Proposal] = []
    add = k.store.add

    def observe(proposal: Proposal) -> None:
        observed.append(proposal)
        add(proposal)

    k.store.add = observe  # type: ignore[method-assign]
    req = SubmitProposalRequest(
        kind=ProposalKind.CREATE,
        content=new_content(body=body, summary="short" if body == "marked invalid" else new_content().summary),
    )
    out = intake(k).execute(req, AGENT)
    assert {f.name for f in fields(out)} == {"proposal_id", "valid"}
    assert out.valid is ok
    assert len(observed) == 1
    row = observed[0]
    assert row == k.stored(out.proposal_id)
    assert row.source_agent == "claude-code"
    assert row.status is (ProposalStatus.NEEDS_OWNER if ok else ProposalStatus.REJECTED)
    if not ok:
        assert body not in repr(row)
    assert k.commits == before


def test_intake_requires_authenticated_token_and_interruption_discards_staged_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    k = Kit()
    req = SubmitProposalRequest(kind=ProposalKind.CREATE, content=new_content())
    with pytest.raises(PermissionDenied):
        intake(k).execute(req, agent_ctx())
    with pytest.raises(PermissionDenied):
        intake(k).execute(req, owner_ctx())

    def stop(*args: object) -> None:
        raise RuntimeError("interrupted")

    monkeypatch.setattr(k.curator, "curate", stop)
    with pytest.raises(RuntimeError, match="interrupted"):
        intake(k).execute(req, AGENT)
    assert k.store.find() == ()


def test_amend_validates_before_saving_and_preserves_provenance() -> None:
    k = Kit()
    out = intake(k).execute(SubmitProposalRequest(kind=ProposalKind.CREATE, content=new_content()), AGENT)
    before = k.commits
    original = k.stored(out.proposal_id)
    amend = AmendProposal(k.store, k.validate_uc(), k.clock)
    req = AmendProposalRequest(proposal_id=original.id, expected_revision=0, body="Reviewed replacement.")
    for denied in (AGENT, owner_ctx(Sensitivity.INTERNAL)):
        with pytest.raises(PermissionDenied):
            amend.execute(req, denied)
    with pytest.raises(AmendmentValidationError) as exc:
        amend.execute(replace(req, body="-----BEGIN PRIVATE KEY-----"), owner_ctx())
    assert ViolationCode.SECRET_DETECTED in exc.value.report.codes
    assert "PRIVATE KEY" not in str(exc.value)
    assert k.stored(original.id) == original
    amended = amend.execute(req, owner_ctx())
    assert amended.revision == 1
    updated = k.stored(original.id)
    assert updated.content.body == req.body
    assert (updated.decision, updated.source_agent, updated.curated_by, updated.base_hash) == (
        original.decision,
        original.source_agent,
        original.curated_by,
        original.base_hash,
    )
    with pytest.raises(ProposalConflict):
        amend.execute(req, owner_ctx())
    assert k.commits == before


@pytest.mark.parametrize("ref", ["vault-layout", "absent-note"])
@pytest.mark.parametrize("field", ["body", "related", "hub", "secret_related"])
def test_hidden_and_missing_content_references_are_scrubbed_and_rejected(ref: str, field: str) -> None:
    k = Kit()

    values: dict[str, object] = {"body": f"See [[{ref}]].", "related": (NoteId(ref),), "hub": NoteId(ref)}
    if field == "secret_related":
        content = new_content(related=(NoteId(ref),), rationale="-----BEGIN PRIVATE KEY-----")
    else:
        content = new_content(**{field: values[field]})
    req = SubmitProposalRequest(kind=ProposalKind.CREATE, content=content)
    outcome = intake(k).execute(req, AGENT)
    assert not outcome.valid
    stored = k.stored(outcome.proposal_id)
    assert stored.status is ProposalStatus.REJECTED
    assert stored.content.body == ""
    assert stored.content.related == ()
    assert ref not in repr(stored)


def test_unexpected_curator_output_never_reaches_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    k = Kit(curator=FakeCurator(Decision.create(NoteId("new-guide"))))

    def refuse(*args: object, **kwargs: object) -> None:
        pytest.fail("unexpected curator result reached durable admission")

    monkeypatch.setattr(k.store, "admit", refuse)
    with pytest.raises(InvalidValueError):
        intake(k).execute(SubmitProposalRequest(kind=ProposalKind.CREATE, content=new_content()), AGENT)
    assert k.store.find() == ()


@pytest.mark.parametrize("secret", [True, False])
def test_rejected_link_uses_a_placeholder_for_required_metadata(secret: bool) -> None:
    k = Kit()
    target = NoteId("server-ssh-access")
    base = k.world.notes.get(target)
    assert base
    request = SubmitProposalRequest(
        kind=ProposalKind.LINK,
        target=target,
        base_hash=content_hash(base),
        content=ProposedContent(
            related=(NoteId("vault-layout"),), rationale="-----BEGIN PRIVATE KEY-----" if secret else "link"
        ),
    )
    result = intake(k).execute(request, AGENT)
    assert not result.valid
    assert k.stored(result.proposal_id).content.related == (NoteId("redacted-reference"),)


def test_manual_rejection_scrubs_text_and_retains_reviewed_link_audit() -> None:
    k = Kit()
    target = NoteId("server-ssh-access")
    related = (NoteId("infrastructure"),)
    base = k.world.notes.get(target)
    assert base
    request = SubmitProposalRequest(
        kind=ProposalKind.LINK,
        target=target,
        base_hash=content_hash(base),
        source_session="fixture-session",
        content=ProposedContent(related=related, rationale="reviewed rationale"),
    )
    admitted = intake(k).execute(request, AGENT)
    assert admitted.valid
    k.review(admitted.proposal_id, ReviewAction.REJECT, reason="Already documented.", expected_revision=0)
    declined = k.stored(admitted.proposal_id)
    assert declined.status is ProposalStatus.REJECTED
    assert declined.content.related == related
    assert declined.target == target
    assert declined.base_hash == content_hash(base)
    assert declined.content.rationale == ""
    assert declined.source_session is None
    assert declined.source_agent == "claude-code"
    assert declined.curated_by == "curator/null"
    assert declined.decision == Decision.reject("Already documented.")
