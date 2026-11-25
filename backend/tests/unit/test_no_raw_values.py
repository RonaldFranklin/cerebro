"""Errors and report messages never echo a received value, only field names, types and lengths."""

from collections.abc import Callable
from typing import Any

import pytest

from cerebro.application.dto import SearchNotesRequest
from cerebro.application.errors import (
    AlreadyExists,
    NoteNotFound,
    ProposalConflict,
    ProposalNotFound,
    SectionNotFound,
    TokenNotFound,
)
from cerebro.domain.access import Scope, validate_agent_name
from cerebro.domain.agent_token import AgentToken
from cerebro.domain.commit_message import CommitMessage
from cerebro.domain.cross_note import cross_note_findings
from cerebro.domain.decision import Decision, DecisionKind
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.frontmatter import parse_note
from cerebro.domain.note import NoteId
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.report import PROPOSAL_POLICY, ValidationReport
from cerebro.domain.token_hash import TokenHash
from cerebro.domain.validation import validate_note
from cerebro.domain.vocabulary import Area, NoteType
from tests.unit.domain.builders import VOCABULARY, make_note, make_raw

SECRET = "sk-SECRET-VALUE-123"
SLUG_SECRET = SECRET.lower()  # a secret that is also a valid kebab-case id/type/area
LONG_SECRET = SECRET * 400
MARKERS = (SECRET.lower(), "secret-value")


def _assert_clean(*texts: object) -> None:
    for text in texts:
        assert not any(m in str(text).lower() for m in MARKERS), text


def _exception_texts(exc: BaseException) -> list[object]:
    return [str(exc), repr(exc), exc.args, getattr(exc, "message", ""), getattr(exc, "field", "")]


RAISING: list[tuple[str, Callable[[], Any]]] = [
    ("note id kebab", lambda: NoteId(SECRET)),
    ("note id type", lambda: NoteId(object())),  # type: ignore[arg-type]
    ("note id long", lambda: NoteId(LONG_SECRET)),
    ("area", lambda: Area(SECRET)),
    ("note type", lambda: NoteType(SECRET)),
    ("priority", lambda: Priority.parse(SECRET)),
    ("sensitivity", lambda: Sensitivity.parse(SECRET)),
    ("status", lambda: Status.parse(SECRET)),
    ("scope", lambda: Scope.parse(SECRET)),
    ("agent name", lambda: validate_agent_name(SECRET)),
    ("token hash", lambda: TokenHash(SECRET)),
    ("token hash long", lambda: TokenHash(LONG_SECRET)),
    ("search text", lambda: SearchNotesRequest(text=1, limit=SECRET)),  # type: ignore[arg-type]
    ("search limit", lambda: SearchNotesRequest(limit=SECRET)),  # type: ignore[arg-type]
    ("decision reason", lambda: Decision(kind=DecisionKind.REJECT, reason=LONG_SECRET)),
    ("decision kind", lambda: Decision(kind=SECRET, reason="x")),  # type: ignore[arg-type]
    (
        "commit message",
        lambda: CommitMessage(
            subject=SECRET + "\n", proposal_id=SECRET + " ", proposed_by=SECRET + "!", curated_by="a", approved_by="b"
        ),
    ),
    ("note title type", lambda: make_note(title=SECRET.encode())),
    ("note date", lambda: make_note(created=SECRET)),
    ("note hub", lambda: make_note(hub=SECRET)),
    (
        "agent token",
        lambda: AgentToken(
            id=SECRET + " ",
            agent=SECRET,
            ceiling=SECRET,  # type: ignore[arg-type]
            scopes=SECRET,  # type: ignore[arg-type]
            token_hash=SECRET,  # type: ignore[arg-type]
            created_at=SECRET,  # type: ignore[arg-type]
        ),
    ),
    ("note not found", lambda: (_ for _ in ()).throw(NoteNotFound(NoteId(SLUG_SECRET)))),
    ("proposal not found", lambda: (_ for _ in ()).throw(ProposalNotFound(SECRET))),
    ("proposal conflict", lambda: (_ for _ in ()).throw(ProposalConflict(SECRET))),
    ("token not found", lambda: (_ for _ in ()).throw(TokenNotFound(SECRET))),
    ("already exists", lambda: (_ for _ in ()).throw(AlreadyExists("token", SECRET))),
    ("section not found", lambda: (_ for _ in ()).throw(SectionNotFound(NoteId(SLUG_SECRET), SECRET))),
]


@pytest.mark.parametrize("build", [b for _, b in RAISING], ids=[n for n, _ in RAISING])
def test_exceptions_never_carry_the_received_value(build: Callable[[], Any]) -> None:
    with pytest.raises(Exception) as excinfo:  # noqa: PT011
        build()
    _assert_clean(*_exception_texts(excinfo.value))


@pytest.mark.parametrize("field", ["id", "title", "type", "area", "priority", "sensitivity", "status", "summary"])
@pytest.mark.parametrize("value", [SECRET, SLUG_SECRET, 123, True, [SECRET], {SECRET: 1}])
def test_frontmatter_reports_never_carry_the_received_value(field: str, value: object) -> None:
    result = parse_note(make_raw(**{field: value}), "body")
    if isinstance(result, ValidationReport):
        _assert_clean(*(v.message for v in result.violations), *(v.field for v in result.violations))


def test_frontmatter_unknown_and_date_fields_never_echo() -> None:
    for raw in (
        make_raw(**{SECRET: "x", SLUG_SECRET: "y"}),
        make_raw(created=SECRET, updated=SLUG_SECRET, review_by=SECRET, related=SECRET, hub=[SECRET]),
        {SECRET: 1, 7: 2},
    ):
        report = parse_note(raw, "body")
        assert isinstance(report, ValidationReport)
        _assert_clean(*(v.message for v in report.violations), *(v.field for v in report.violations))
    assert isinstance(parse_note([SECRET], "body"), ValidationReport)
    assert isinstance(parse_note(SECRET, "body"), ValidationReport)


def test_validation_findings_never_carry_received_ids_or_vocabulary() -> None:
    note = make_note(
        type=NoteType(SLUG_SECRET),
        area=Area(SLUG_SECRET),
        hub=NoteId(SLUG_SECRET),
        related=(NoteId(SLUG_SECRET + "-a"),),
        superseded_by=NoteId(SLUG_SECRET + "-b"),
        body=f"see [[{SLUG_SECRET}-c]] and [[{SLUG_SECRET}-d#Part]]",
    )
    report = validate_note(note, VOCABULARY, PROPOSAL_POLICY)
    _assert_clean(*(v.message for v in report.violations), *(v.field for v in report.violations))
    findings = cross_note_findings(NoteRef.from_note(note), {})
    assert findings
    _assert_clean(*(f.message for f in findings), *(f.field for f in findings))


def test_hub_and_sensitivity_findings_do_not_name_the_target() -> None:
    target = NoteRef.from_note(
        make_note(id=NoteId(SLUG_SECRET), type=NoteType("runbook"), sensitivity=Sensitivity.RESTRICTED, hub=None)
    )
    source = NoteRef.from_note(
        make_note(
            id=NoteId("a-note"),
            sensitivity=Sensitivity.PUBLIC,
            hub=NoteId(SLUG_SECRET),
            related=(NoteId(SLUG_SECRET),),
            body=f"[[{SLUG_SECRET}]]",
        )
    )
    findings = cross_note_findings(source, {target.id: target, source.id: source})
    assert findings
    _assert_clean(*(f.message for f in findings))
