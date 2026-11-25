from collections.abc import Iterable

import pytest

from cerebro.domain.cross_note import MAX_HUB_CHAIN_DEPTH, Snapshot, cross_note_findings
from cerebro.domain.enums import Priority, Status
from cerebro.domain.links import WikiLink
from cerebro.domain.note import NoteId
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.report import OWNER_EDIT_POLICY, PROPOSAL_POLICY, Severity, SeverityPolicy, ViolationCode
from cerebro.domain.vocabulary import HUB, NoteType
from tests.unit.domain.builders import make_note


def nid(value: str) -> NoteId:
    return NoteId(value)


def ref(
    id_: str,
    *,
    hub: str | None = None,
    is_hub: bool = False,
    related: Iterable[str] = (),
    superseded_by: str | None = None,
    body_links: Iterable[str] = (),
) -> NoteRef:
    note = make_note(
        id=nid(id_),
        type=HUB if is_hub else NoteType("runbook"),
        priority=Priority.P1 if is_hub else Priority.P2,
        hub=nid(hub) if hub else None,
        related=tuple(nid(r) for r in related),
        superseded_by=nid(superseded_by) if superseded_by else None,
        status=Status.SUPERSEDED if superseded_by else Status.VERIFIED,
        body=" ".join(f"[[{b}]]" for b in body_links),
    )
    return NoteRef.from_note(note)


def snap(*refs: NoteRef) -> Snapshot:
    return {r.id: r for r in refs}


def codes(target: NoteRef, snapshot: Snapshot) -> list[ViolationCode]:
    return [f.code for f in cross_note_findings(target, snapshot)]


def test_note_ref_is_a_body_free_view() -> None:
    note = make_note(body="see [[alpha#Part]]")
    r = NoteRef.from_note(note)
    assert r.body_links == (WikiLink(target=nid("alpha"), section="Part"),)
    assert not hasattr(r, "body")
    assert not r.is_hub
    assert ref("h", is_hub=True).is_hub
    assert [link.target.value for link in r.links][:1] == ["alpha"]


def test_clean_note_has_no_findings() -> None:
    hub = ref("area", is_hub=True)
    other = ref("other", hub="area")
    me = ref("me", hub="area", related=["other"], body_links=["other"])
    assert codes(me, snap(hub, other, me)) == []


def test_broken_links_in_body_related_and_superseded_by() -> None:
    hub = ref("area", is_hub=True)
    me = ref("me", hub="area", related=["gone-r"], superseded_by="gone-s", body_links=["gone-b"])
    findings = cross_note_findings(me, snap(hub, me))
    assert {(f.code, f.field) for f in findings} == {
        (ViolationCode.BROKEN_LINK, "body"),
        (ViolationCode.BROKEN_LINK, "related"),
        (ViolationCode.BROKEN_LINK, "superseded_by"),
    }


def test_broken_link_is_warning_for_owner_and_error_for_proposal() -> None:
    hub = ref("area", is_hub=True)
    me = ref("me", hub="area", body_links=["gone"])
    findings = cross_note_findings(me, snap(hub, me))
    assert [v.severity for v in OWNER_EDIT_POLICY.report(findings).violations] == [Severity.WARNING]
    assert [v.severity for v in PROPOSAL_POLICY.report(findings).violations] == [Severity.ERROR]


def test_link_to_itself_is_not_broken_even_if_absent_from_snapshot() -> None:
    hub = ref("area", is_hub=True)
    me = ref("me", hub="area", body_links=["me"])
    assert codes(me, snap(hub)) == []


def test_missing_hub_is_an_error_for_every_policy() -> None:
    me = ref("me", hub="area")
    findings = cross_note_findings(me, snap(me))
    assert [f.code for f in findings] == [ViolationCode.HUB_NOT_FOUND]
    assert OWNER_EDIT_POLICY.report(findings).errors
    assert PROPOSAL_POLICY.report(findings).errors


def test_hub_must_have_type_hub() -> None:
    plain = ref("plain")
    me = ref("me", hub="plain")
    assert codes(me, snap(plain, me)) == [ViolationCode.HUB_NOT_A_HUB]


def test_note_without_hub_has_no_hub_findings() -> None:
    assert codes(ref("me"), snap(ref("me"))) == []


def test_superseded_by_cycle_through_the_note() -> None:
    a = ref("a", superseded_by="b")
    b = ref("b", superseded_by="c")
    c = ref("c", superseded_by="a")
    findings = cross_note_findings(a, snap(a, b, c))
    assert [f.code for f in findings] == [ViolationCode.SUPERSEDED_BY_CYCLE]
    assert findings[0].message == "a -> b -> c -> a"


def test_two_note_superseded_by_cycle() -> None:
    a = ref("a", superseded_by="b")
    b = ref("b", superseded_by="a")
    assert codes(a, snap(a, b)) == [ViolationCode.SUPERSEDED_BY_CYCLE]


def test_superseded_by_chain_without_cycle_is_fine() -> None:
    a = ref("a", superseded_by="b")
    b = ref("b", superseded_by="c")
    c = ref("c")
    assert codes(a, snap(a, b, c)) == []


def test_cycle_not_including_the_note_is_reported_on_its_members_only() -> None:
    a = ref("a", superseded_by="b")
    b = ref("b", superseded_by="c")
    c = ref("c", superseded_by="b")
    assert codes(a, snap(a, b, c)) == []
    assert codes(b, snap(a, b, c)) == [ViolationCode.SUPERSEDED_BY_CYCLE]


def test_direct_self_reference_is_left_to_the_per_note_rule() -> None:
    a = ref("a", superseded_by="a")
    assert codes(a, snap(a)) == []
    h = ref("h", is_hub=True, hub="h")
    assert codes(h, snap(h)) == []


def test_superseded_by_chain_ending_at_missing_note_stops() -> None:
    a = ref("a", superseded_by="b")
    b = ref("b", superseded_by="ghost")
    assert codes(a, snap(a, b)) == []


def test_hub_chain_of_three_hubs_is_allowed() -> None:
    h1 = ref("h1", is_hub=True)
    h2 = ref("h2", is_hub=True, hub="h1")
    h3 = ref("h3", is_hub=True, hub="h2")
    assert MAX_HUB_CHAIN_DEPTH == 3
    assert codes(h3, snap(h1, h2, h3)) == []


def test_hub_chain_of_four_hubs_is_too_deep() -> None:
    h1 = ref("h1", is_hub=True)
    h2 = ref("h2", is_hub=True, hub="h1")
    h3 = ref("h3", is_hub=True, hub="h2")
    h4 = ref("h4", is_hub=True, hub="h3")
    findings = cross_note_findings(h4, snap(h1, h2, h3, h4))
    assert [f.code for f in findings] == [ViolationCode.HUB_CHAIN_TOO_DEEP]
    assert "h4 -> h3 -> h2 -> h1" in findings[0].message


def test_regular_note_counts_only_the_hubs_above_it() -> None:
    h1 = ref("h1", is_hub=True)
    h2 = ref("h2", is_hub=True, hub="h1")
    h3 = ref("h3", is_hub=True, hub="h2")
    h4 = ref("h4", is_hub=True, hub="h3")
    ok = ref("ok", hub="h3")
    deep = ref("deep", hub="h4")
    everything = snap(h1, h2, h3, h4, ok, deep)
    assert codes(ok, everything) == []
    assert codes(deep, everything) == [ViolationCode.HUB_CHAIN_TOO_DEEP]


def test_hub_chain_cycle() -> None:
    h1 = ref("h1", is_hub=True, hub="h2")
    h2 = ref("h2", is_hub=True, hub="h1")
    findings = cross_note_findings(h1, snap(h1, h2))
    assert [f.code for f in findings] == [ViolationCode.HUB_CHAIN_CYCLE]
    assert findings[0].message == "h1 -> h2 -> h1"


def test_regular_note_reaching_a_hub_cycle_is_reported() -> None:
    h1 = ref("h1", is_hub=True, hub="h2")
    h2 = ref("h2", is_hub=True, hub="h1")
    me = ref("me", hub="h1")
    assert codes(me, snap(h1, h2, me)) == [ViolationCode.HUB_CHAIN_CYCLE]


@pytest.mark.parametrize("policy", [PROPOSAL_POLICY, OWNER_EDIT_POLICY])
def test_chain_and_cycle_rules_are_errors_on_every_path(policy: SeverityPolicy) -> None:
    h1 = ref("h1", is_hub=True, hub="h2")
    h2 = ref("h2", is_hub=True, hub="h1")
    a = ref("a", superseded_by="b")
    b = ref("b", superseded_by="a")
    snapshot = snap(h1, h2, a, b)
    for target in (h1, a):
        report = policy.report(cross_note_findings(target, snapshot))
        assert report.errors
