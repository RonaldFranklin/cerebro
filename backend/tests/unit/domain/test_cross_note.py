from collections.abc import Iterable

import pytest

from cerebro.domain.cross_note import (
    MAX_HUB_CHAIN_DEPTH,
    Snapshot,
    cross_note_findings,
    inbound_link_sensitivity_findings,
)
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.links import WikiLink
from cerebro.domain.note import NoteId
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.report import AGENT_WRITE_POLICY, OWNER_EDIT_POLICY, Severity, SeverityPolicy, ViolationCode
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
    sensitivity: Sensitivity = Sensitivity.RESTRICTED,
) -> NoteRef:
    note = make_note(
        id=nid(id_),
        type=HUB if is_hub else NoteType("runbook"),
        priority=Priority.P1 if is_hub else Priority.P2,
        hub=nid(hub) if hub else None,
        related=tuple(nid(r) for r in related),
        superseded_by=nid(superseded_by) if superseded_by else None,
        status=Status.SUPERSEDED if superseded_by else Status.VERIFIED,
        sensitivity=sensitivity,
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


def test_broken_link_is_warning_for_owner_and_error_for_agent_write() -> None:
    hub = ref("area", is_hub=True)
    me = ref("me", hub="area", body_links=["gone"])
    findings = cross_note_findings(me, snap(hub, me))
    assert [v.severity for v in OWNER_EDIT_POLICY.report(findings).violations] == [Severity.WARNING]
    assert [v.severity for v in AGENT_WRITE_POLICY.report(findings).violations] == [Severity.ERROR]


def test_link_to_itself_is_not_broken_even_if_absent_from_snapshot() -> None:
    hub = ref("area", is_hub=True)
    me = ref("me", hub="area", body_links=["me"])
    assert codes(me, snap(hub)) == []


def test_missing_hub_is_an_error_for_every_policy() -> None:
    me = ref("me", hub="area")
    findings = cross_note_findings(me, snap(me))
    assert [f.code for f in findings] == [ViolationCode.HUB_NOT_FOUND]
    assert OWNER_EDIT_POLICY.report(findings).errors
    assert AGENT_WRITE_POLICY.report(findings).errors


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
    assert findings[0].message == "superseded_by chain of 3 notes leads back to this note"


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
    assert findings[0].message == "4 hubs deep (maximum 3)"


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
    assert findings[0].message == "hub chain loops back on itself after 2 notes"


def test_regular_note_reaching_a_hub_cycle_is_reported() -> None:
    h1 = ref("h1", is_hub=True, hub="h2")
    h2 = ref("h2", is_hub=True, hub="h1")
    me = ref("me", hub="h1")
    assert codes(me, snap(h1, h2, me)) == [ViolationCode.HUB_CHAIN_CYCLE]


@pytest.mark.parametrize("policy", [AGENT_WRITE_POLICY, OWNER_EDIT_POLICY])
def test_chain_and_cycle_rules_are_errors_on_every_path(policy: SeverityPolicy) -> None:
    h1 = ref("h1", is_hub=True, hub="h2")
    h2 = ref("h2", is_hub=True, hub="h1")
    a = ref("a", superseded_by="b")
    b = ref("b", superseded_by="a")
    snapshot = snap(h1, h2, a, b)
    for target in (h1, a):
        report = policy.report(cross_note_findings(target, snapshot))
        assert report.errors


# -- links to a note of higher sensitivity ------------------------------------------------------------------------

PUB, INT, RES = Sensitivity.PUBLIC, Sensitivity.INTERNAL, Sensitivity.RESTRICTED


def leak_fields(target: NoteRef, snapshot: Snapshot) -> list[tuple[ViolationCode, str | None]]:
    return [(f.code, f.field) for f in cross_note_findings(target, snapshot)]


@pytest.mark.parametrize(
    ("kind", "field"),
    [("body", "body"), ("related", "related"), ("superseded_by", "superseded_by"), ("hub", "hub")],
)
def test_each_link_kind_to_a_higher_sensitivity_is_reported(kind: str, field: str) -> None:
    secret = ref("secret", is_hub=kind == "hub", sensitivity=RES)
    args: dict[str, object] = {
        "body": {"body_links": ["secret"]},
        "related": {"related": ["secret"]},
        "superseded_by": {"superseded_by": "secret"},
        "hub": {"hub": "secret"},
    }[kind]  # type: ignore[assignment]
    me = ref("me", sensitivity=INT, **args)  # type: ignore[arg-type]
    assert (ViolationCode.LINK_TO_HIGHER_SENSITIVITY, field) in leak_fields(me, snap(secret, me))


@pytest.mark.parametrize(
    ("mine", "theirs", "leaks"),
    [
        (PUB, PUB, False),
        (INT, INT, False),
        (RES, RES, False),
        (INT, PUB, False),
        (RES, INT, False),
        (RES, PUB, False),
        (PUB, INT, True),
        (INT, RES, True),
        (PUB, RES, True),
    ],
)
def test_only_links_to_a_strictly_higher_sensitivity_are_reported(
    mine: Sensitivity, theirs: Sensitivity, leaks: bool
) -> None:
    other = ref("other", sensitivity=theirs)
    me = ref("me", sensitivity=mine, related=["other"], body_links=["other"])
    found = [c for c, _ in leak_fields(me, snap(other, me)) if c is ViolationCode.LINK_TO_HIGHER_SENSITIVITY]
    assert bool(found) is leaks


def test_the_rule_is_an_error_on_every_policy_and_never_a_warning() -> None:
    secret = ref("secret", sensitivity=RES)
    me = ref("me", sensitivity=PUB, body_links=["secret"])
    findings = [
        f for f in cross_note_findings(me, snap(secret, me)) if f.code is ViolationCode.LINK_TO_HIGHER_SENSITIVITY
    ]
    assert findings
    for policy in (AGENT_WRITE_POLICY, OWNER_EDIT_POLICY):
        assert ViolationCode.LINK_TO_HIGHER_SENSITIVITY not in policy.warnings
        assert [v.severity for v in policy.report(findings).violations] == [Severity.ERROR]


def test_a_missing_target_is_a_broken_link_not_a_sensitivity_finding() -> None:
    me = ref("me", sensitivity=PUB, body_links=["gone"])
    assert [c for c, _ in leak_fields(me, snap(me))] == [ViolationCode.BROKEN_LINK]


def test_repeated_links_to_the_same_target_are_reported_once_per_kind() -> None:
    secret = ref("secret", sensitivity=RES)
    me = ref("me", sensitivity=PUB, body_links=["secret", "secret"], related=["secret"])
    found = [(c, f) for c, f in leak_fields(me, snap(secret, me)) if c is ViolationCode.LINK_TO_HIGHER_SENSITIVITY]
    assert found == [
        (ViolationCode.LINK_TO_HIGHER_SENSITIVITY, "body"),
        (ViolationCode.LINK_TO_HIGHER_SENSITIVITY, "related"),
    ]


def test_the_message_names_nothing_about_the_target() -> None:
    secret = NoteRef.from_note(
        make_note(id=nid("secret"), title="Vault master passphrase", sensitivity=RES, hub=None, related=())
    )
    me = ref("me", sensitivity=PUB, body_links=["secret"])
    (finding,) = [
        f for f in cross_note_findings(me, snap(secret, me)) if f.code is ViolationCode.LINK_TO_HIGHER_SENSITIVITY
    ]
    for revealing in ("secret", "Vault", "passphrase", "restricted", "internal", "higher", "sensitivity"):
        assert revealing not in finding.message


@pytest.mark.parametrize(
    "body", ["`[[secret]]`", "```\n[[secret]]\n```", "[[secret|alias]]", "[[ secret # Part | x ]]"]
)
def test_code_span_and_alias_links_are_covered_by_the_rule(body: str) -> None:
    secret = ref("secret", sensitivity=RES)
    me = NoteRef.from_note(make_note(id=nid("me"), sensitivity=PUB, hub=None, related=(), body=body))
    assert (ViolationCode.LINK_TO_HIGHER_SENSITIVITY, "body") in leak_fields(me, snap(secret, me))


def test_inbound_findings_name_lower_sensitivity_linkers_only() -> None:
    target = ref("target", sensitivity=RES)
    low = ref("low", sensitivity=PUB, related=["target"])
    equal = ref("equal", sensitivity=RES, related=["target"])
    unrelated = ref("unrelated", sensitivity=PUB)
    findings = inbound_link_sensitivity_findings(target, snap(target, low, equal, unrelated))
    assert [(f.code, f.message) for f in findings] == [
        (ViolationCode.LINK_TO_HIGHER_SENSITIVITY, "low links to this note")
    ]
