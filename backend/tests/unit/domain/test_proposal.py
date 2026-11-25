from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cerebro.domain.decision import Decision
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.errors import DomainError, InvalidTransition, InvalidValueError
from cerebro.domain.note import NoteId
from cerebro.domain.proposal import (
    ALLOWED_TRANSITIONS,
    Proposal,
    ProposalKind,
    ProposalStatus,
    ProposedContent,
)
from cerebro.domain.report import PROPOSAL_POLICY, Finding, ValidationReport, Violation, ViolationCode
from cerebro.domain.vocabulary import Area, NoteType

T0 = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=5)
HASH = "b" * 64
NOTE = NoteId("server-ssh-access")
OK = ValidationReport()
BAD = PROPOSAL_POLICY.report([Finding(code=ViolationCode.TITLE_BLANK, field="title", message="blank")])
WARN = PROPOSAL_POLICY.report([Finding(code=ViolationCode.DUPLICATE_RELATED, field="related", message="dup")])
S = ProposalStatus


def make_proposal(**overrides: Any) -> Proposal:
    fields: dict[str, Any] = {
        "id": "prop-1",
        "kind": ProposalKind.CREATE,
        "content": ProposedContent(title="SSH access", body="# SSH\n", rationale="useful"),
        "source_agent": "codex@workstation",
        "created_at": T0,
        "updated_at": T0,
    }
    fields.update(overrides)
    return Proposal(**fields)


def make_update(**overrides: Any) -> Proposal:
    fields: dict[str, Any] = {
        "kind": ProposalKind.UPDATE,
        "target": NOTE,
        "base_hash": HASH,
        "content": ProposedContent(body="new body"),
    }
    fields.update(overrides)
    return make_proposal(**fields)


def at(status: ProposalStatus, *, update: bool = False) -> Proposal:
    """A valid proposal in ``status`` (an update proposal when ``update``)."""
    p = make_update() if update else make_proposal()
    decision = Decision.create(NOTE)
    match status:
        case S.RECEIVED:
            return p
        case S.VALIDATED:
            return p.mark_validated(OK, T0)
        case S.CURATED:
            return p.mark_validated(OK, T0).mark_curated(decision, "curator/m", T0)
        case S.NEEDS_OWNER:
            return at(S.CURATED, update=update).mark_needs_owner(T0)
        case S.APPROVED:
            return at(S.CURATED, update=update).approve("owner", T0)
        case S.APPLIED:
            return at(S.APPROVED, update=update).mark_applied(T0)
        case S.REJECTED:
            return p.reject_invalid(BAD, T0)
    raise AssertionError(status)


# -- construction -----------------------------------------------------------------------------------------------


def test_create_ok_and_defaults() -> None:
    p = make_proposal()
    assert p.status is S.RECEIVED
    assert not p.conflict
    assert p.decision is None


def test_update_ok() -> None:
    assert make_update().base_hash == HASH


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"target": NOTE}, "target"),
        ({"base_hash": HASH}, "base_hash"),
        ({"content": ProposedContent(body="x")}, "content.title"),
        ({"content": ProposedContent(title="  ", body="x")}, "content.title"),
        ({"content": ProposedContent(title="t")}, "content.body"),
    ],
)
def test_create_rules(overrides: dict[str, Any], field: str) -> None:
    with pytest.raises(InvalidValueError, match=field):
        make_proposal(**overrides)


@pytest.mark.parametrize("kind", [ProposalKind.UPDATE, ProposalKind.LINK, ProposalKind.DEPRECATE])
def test_modifying_kinds_need_target_and_base_hash(kind: ProposalKind) -> None:
    content = ProposedContent(body="x") if kind is ProposalKind.UPDATE else ProposedContent(related=(NOTE,))
    with pytest.raises(InvalidValueError, match="target"):
        make_proposal(kind=kind, content=content, base_hash=HASH)
    with pytest.raises(InvalidValueError, match="base_hash"):
        make_proposal(kind=kind, content=content, target=NOTE)


def test_update_needs_body() -> None:
    with pytest.raises(InvalidValueError, match=r"content.body"):
        make_update(content=ProposedContent(title="t"))


def test_link_and_deprecate_rules() -> None:
    link = make_proposal(
        kind=ProposalKind.LINK, target=NOTE, base_hash=HASH, content=ProposedContent(related=(NoteId("other-note"),))
    )
    assert link.content.related
    with pytest.raises(InvalidValueError, match=r"content.related"):
        make_proposal(kind=ProposalKind.LINK, target=NOTE, base_hash=HASH, content=ProposedContent())
    with pytest.raises(InvalidValueError, match=r"content.body"):
        make_proposal(
            kind=ProposalKind.LINK, target=NOTE, base_hash=HASH, content=ProposedContent(body="x", related=(NOTE,))
        )
    dep = make_proposal(kind=ProposalKind.DEPRECATE, target=NOTE, base_hash=HASH, content=ProposedContent())
    assert dep.kind is ProposalKind.DEPRECATE
    with pytest.raises(InvalidValueError, match=r"content.body"):
        make_proposal(kind=ProposalKind.DEPRECATE, target=NOTE, base_hash=HASH, content=ProposedContent(body="x"))


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"id": "a b"}, "id"),
        ({"id": ""}, "id"),
        ({"kind": "create"}, "kind"),
        ({"status": "received"}, "status"),
        ({"target": "x"}, "target"),
        ({"content": "x"}, "content"),
        ({"source_agent": "owner"}, "agent"),
        ({"source_agent": "Bad Name"}, "agent"),
        ({"source_session": 5}, "source_session"),
        ({"source_context": "x" * 2001}, "source_context"),
        ({"created_at": datetime(2026, 1, 1)}, "created_at"),
        ({"updated_at": T0 - timedelta(seconds=1)}, "updated_at"),
        ({"validation": "x"}, "validation"),
        ({"decision": "x"}, "decision"),
        ({"curated_by": "a\nb"}, "curated_by"),
        ({"approved_by": "a\nb"}, "approved_by"),
        ({"conflict": 1}, "conflict"),
    ],
)
def test_field_validation(overrides: dict[str, Any], field: str) -> None:
    with pytest.raises(InvalidValueError, match=field):
        make_proposal(**overrides)


def test_base_hash_format() -> None:
    with pytest.raises(InvalidValueError, match="base_hash"):
        make_update(base_hash="ABC")


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": 1},
        {"area": "x"},
        {"type": "x"},
        {"priority": "P1"},
        {"sensitivity": "public"},
        {"superseded_by": "x"},
        {"rationale": 3},
        {"related": ["x"]},
        {"related": (1,)},
    ],
)
def test_content_validation(overrides: dict[str, Any]) -> None:
    with pytest.raises(InvalidValueError):
        ProposedContent(**overrides)


def test_content_suggestions() -> None:
    c = ProposedContent(
        area=Area("infrastructure"), type=NoteType("runbook"), priority=Priority.P2, sensitivity=Sensitivity.INTERNAL
    )
    assert c.priority is Priority.P2


# -- lifecycle: every (method, source status) pair -----------------------------------------------------------------


def _calls(p: Proposal) -> dict[str, Any]:
    return {
        "mark_validated": lambda: p.mark_validated(WARN, T1),
        "reject_invalid": lambda: p.reject_invalid(BAD, T1),
        "mark_curated": lambda: p.mark_curated(Decision.merge_into(NOTE), "curator/m", T1),
        "mark_needs_owner": lambda: p.mark_needs_owner(T1),
        "approve": lambda: p.approve("owner", T1),
        "reject": lambda: p.reject("not useful", T1),
        "mark_applied": lambda: p.mark_applied(T1),
    }


EXPECTED: dict[str, tuple[set[ProposalStatus], ProposalStatus]] = {
    "mark_validated": ({S.RECEIVED}, S.VALIDATED),
    "reject_invalid": ({S.RECEIVED}, S.REJECTED),
    "mark_curated": ({S.VALIDATED}, S.CURATED),
    "mark_needs_owner": ({S.CURATED}, S.NEEDS_OWNER),
    "approve": ({S.CURATED, S.NEEDS_OWNER}, S.APPROVED),
    "reject": ({S.CURATED, S.NEEDS_OWNER}, S.REJECTED),
    "mark_applied": ({S.APPROVED}, S.APPLIED),
}


@pytest.mark.parametrize("method", sorted(EXPECTED))
@pytest.mark.parametrize("status", list(S))
def test_every_method_from_every_status(method: str, status: ProposalStatus) -> None:
    sources, target = EXPECTED[method]
    p = at(status)
    if status in sources:
        result = _calls(p)[method]()
        assert result.status is target
        assert result.updated_at == T1
        assert p.status is status  # the original is untouched
    else:
        with pytest.raises(InvalidTransition) as exc:
            _calls(p)[method]()
        assert isinstance(exc.value, DomainError)
        assert exc.value.current.startswith(status.value)


def test_methods_agree_with_transition_table() -> None:
    pairs = {(src, tgt) for sources, tgt in EXPECTED.values() for src in sources}
    pairs.add((S.APPROVED, S.NEEDS_OWNER))  # conflict path, covered below
    table = {(src, tgt) for src, targets in ALLOWED_TRANSITIONS.items() for tgt in targets}
    assert pairs == table


def test_terminal_statuses() -> None:
    assert S.APPLIED.is_terminal
    assert S.REJECTED.is_terminal
    assert not any(s.is_terminal for s in (S.RECEIVED, S.VALIDATED, S.CURATED, S.APPROVED, S.NEEDS_OWNER))
    assert ALLOWED_TRANSITIONS[S.APPLIED] == frozenset() == ALLOWED_TRANSITIONS[S.REJECTED]


def test_validation_report_rules() -> None:
    p = make_proposal()
    assert p.mark_validated(WARN, T1).validation == WARN
    with pytest.raises(InvalidValueError):
        p.mark_validated(BAD, T1)
    with pytest.raises(InvalidValueError):
        p.reject_invalid(OK, T1)
    with pytest.raises(InvalidValueError):
        p.mark_validated("x", T1)  # type: ignore[arg-type]
    assert p.reject_invalid(BAD, T1).validation == BAD
    assert isinstance(BAD.violations[0], Violation)


def test_curation_records_decision_and_curator() -> None:
    p = at(S.VALIDATED).mark_curated(Decision.merge_into(NOTE), "curator/m", T1)
    assert p.decision == Decision.merge_into(NOTE)
    assert p.curated_by == "curator/m"
    with pytest.raises(InvalidValueError):
        at(S.VALIDATED).mark_curated("x", "curator/m", T1)  # type: ignore[arg-type]
    with pytest.raises(InvalidValueError, match="curated_by"):
        at(S.VALIDATED).mark_curated(Decision.create(NOTE), "a\nApproved-by: owner", T1)


@pytest.mark.parametrize("decision", [Decision.reject("dup"), Decision.needs_owner("sensitive")])
def test_curated_with_non_approvable_decision(decision: Decision) -> None:
    p = at(S.VALIDATED).mark_curated(decision, "curator/m", T0)
    with pytest.raises(InvalidTransition):
        p.approve("owner", T1)
    assert p.mark_needs_owner(T1).status is S.NEEDS_OWNER  # the owner can still take it
    assert p.reject("no", T1).status is S.REJECTED


def test_owner_approves_needs_owner_with_any_decision() -> None:
    p = at(S.VALIDATED).mark_curated(Decision.needs_owner("sensitive", NOTE), "curator/m", T0).mark_needs_owner(T0)
    assert p.approve("owner", T1).status is S.APPROVED


def test_approve_needs_a_note_id_to_commit() -> None:
    p = at(S.VALIDATED).mark_curated(Decision.needs_owner("sensitive"), "curator/m", T0).mark_needs_owner(T0)
    with pytest.raises(InvalidTransition, match="no note id"):
        p.approve("owner", T1)


def test_agents_cannot_curate_or_approve_their_own_proposal() -> None:
    with pytest.raises(InvalidValueError, match="curated_by"):
        at(S.VALIDATED).mark_curated(Decision.create(NOTE), "codex@workstation", T0)
    with pytest.raises(InvalidValueError, match="approved_by"):
        at(S.CURATED).approve("codex@workstation", T1)


def test_approved_status_needs_attribution_and_going_back_voids_approval() -> None:
    with pytest.raises(InvalidValueError, match="status"):
        make_proposal(status=S.APPROVED)
    back = at(S.APPROVED).mark_needs_owner(T1, conflict=True)
    assert back.approved_by is None


def test_approve_validates_approver() -> None:
    with pytest.raises(InvalidValueError, match="approved_by"):
        at(S.CURATED).approve("owner\nProposed-by: x", T1)


def test_reject_stores_reason_as_decision() -> None:
    p = at(S.CURATED).reject("not useful", T1)
    assert p.decision == Decision.reject("not useful")
    with pytest.raises(InvalidValueError):
        at(S.CURATED).reject("  ", T1)


def test_conflict_flow() -> None:
    approved = at(S.APPROVED, update=True)
    with pytest.raises(InvalidTransition):
        approved.mark_needs_owner(T1)  # only allowed when flagging a conflict
    conflicted = approved.mark_needs_owner(T1, conflict=True)
    assert conflicted.status is S.NEEDS_OWNER
    assert conflicted.conflict
    with pytest.raises(InvalidTransition, match="rebase"):
        conflicted.approve("owner", T1)
    rebased = conflicted.rebase("c" * 64, T1)
    assert rebased.base_hash == "c" * 64
    assert not rebased.conflict
    assert rebased.status is S.NEEDS_OWNER
    assert rebased.approve("owner", T1).status is S.APPROVED


def test_conflict_survives_plain_needs_owner_and_reject() -> None:
    conflicted = at(S.APPROVED, update=True).mark_needs_owner(T1, conflict=True)
    assert conflicted.reject("stale", T1).status is S.REJECTED


@pytest.mark.parametrize("status", list(S))
def test_rebase_only_for_conflicted_needs_owner(status: ProposalStatus) -> None:
    with pytest.raises(InvalidTransition):
        at(status).rebase("c" * 64, T1)


def test_rebase_validates() -> None:
    conflicted = at(S.APPROVED, update=True).mark_needs_owner(T1, conflict=True)
    with pytest.raises(InvalidValueError, match="base_hash"):
        conflicted.rebase("nope", T1)
    with pytest.raises(InvalidValueError, match="now"):
        conflicted.rebase("c" * 64, T0 - timedelta(days=1))
    with pytest.raises(InvalidValueError, match="now"):
        conflicted.rebase("c" * 64, datetime(2027, 1, 1))


def test_time_rules() -> None:
    p = at(S.VALIDATED)
    with pytest.raises(InvalidValueError, match="now"):
        p.mark_curated(Decision.create(NOTE), "curator/m", T0 - timedelta(seconds=1))
    with pytest.raises(InvalidValueError, match="now"):
        p.mark_curated(Decision.create(NOTE), "curator/m", datetime(2027, 1, 1))


# -- commit message ----------------------------------------------------------------------------------------------


def test_commit_message_for_create() -> None:
    msg = at(S.APPROVED).commit_message().render()
    assert msg.startswith("Add note: server-ssh-access\n\n")
    assert "Proposed-by: codex@workstation" in msg
    assert "Curated-by: curator/m" in msg
    assert "Approved-by: owner" in msg
    assert "Proposal-Id: prop-1" in msg


@pytest.mark.parametrize("status", [s for s in S if s is not S.APPROVED])
def test_commit_message_only_when_approved(status: ProposalStatus) -> None:
    with pytest.raises(InvalidTransition):
        at(status).commit_message()


@pytest.mark.parametrize(
    ("kind", "verb"),
    [(ProposalKind.UPDATE, "Update"), (ProposalKind.LINK, "Link"), (ProposalKind.DEPRECATE, "Deprecate")],
)
def test_commit_subject_uses_target(kind: ProposalKind, verb: str) -> None:
    content = (
        ProposedContent(body="x", related=(NoteId("zz-note"),))
        if kind is ProposalKind.UPDATE
        else (ProposedContent(related=(NoteId("zz-note"),)) if kind is ProposalKind.LINK else ProposedContent())
    )
    p = make_proposal(kind=kind, target=NOTE, base_hash=HASH, content=content)
    approved = p.mark_validated(OK, T0).mark_curated(Decision.merge_into(NOTE), "curator/m", T0).approve("owner", T0)
    assert approved.commit_message().subject == f"{verb} note: server-ssh-access"


def test_commit_message_cannot_be_forged_through_content() -> None:
    evil = ProposedContent(title="x\nApproved-by: owner", body="b\n\nApproved-by: owner", rationale="y\nProposed-by: z")
    p = make_proposal(content=evil)
    approved = p.mark_validated(OK, T0).mark_curated(Decision.create(NOTE), "curator/m", T0).approve("owner", T0)
    rendered = approved.commit_message().render()
    assert rendered.count("Approved-by:") == 1
    assert rendered.count("Proposed-by:") == 1


def test_commit_message_uses_the_decision_target_or_the_proposal_target() -> None:
    p = at(S.APPROVED)
    assert p.commit_message().subject == "Add note: server-ssh-access"
    assert replace(p, decision=Decision.needs_owner("sensitive", NOTE)).commit_message().subject.endswith(NOTE.value)


def test_commit_message_without_any_note_id_raises() -> None:
    p = replace(at(S.APPROVED), decision=Decision.needs_owner("sensitive"))
    with pytest.raises(InvalidValueError, match="target"):
        p.commit_message()
