"""Errors and report messages never echo a received value, only field names, types and lengths."""

from collections.abc import Callable
from typing import Any

import pytest

from cerebro.application.dto import HealthReportRequest, SearchNotesRequest
from cerebro.application.errors import (
    AlreadyExists,
    NoteNotFound,
    SectionNotFound,
    TokenNotFound,
)
from cerebro.application.use_cases import HealthReport
from cerebro.domain.access import AccessCeiling, Scope, validate_agent_name
from cerebro.domain.agent_token import AgentToken
from cerebro.domain.commit_message import OwnerCommit
from cerebro.domain.cross_note import cross_note_findings
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.frontmatter import parse_note
from cerebro.domain.note import NoteId
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.report import AGENT_WRITE_POLICY, ValidationReport, ViolationCode
from cerebro.domain.token_hash import TokenHash
from cerebro.domain.validation import validate_note
from cerebro.domain.vocabulary import HUB, Area, NoteType
from tests.fakes.system import FixedClock
from tests.unit.application.world import World, ctx
from tests.unit.domain.builders import VOCABULARY, make_note, make_raw

SECRET = "sk-SECRET-VALUE-123"
SLUG_SECRET = SECRET.lower()  # a secret that is also a valid kebab-case id/type/area
LONG_SECRET = SECRET * 400
MARKERS = (SECRET.lower(), "secret-value")


def _assert_clean(*texts: object) -> None:
    for text in texts:
        assert not any(m in str(text).lower() for m in MARKERS), text


def _exception_texts(exc: BaseException) -> list[object]:
    texts: list[object] = [str(exc), repr(exc), exc.args, getattr(exc, "message", ""), getattr(exc, "field", "")]
    texts += list(getattr(exc, "__notes__", []))
    for chained in (exc.__cause__, exc.__context__):
        if chained is not None:
            texts += _exception_texts(chained)
    return texts


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
    ("commit message", lambda: OwnerCommit(subject=SECRET + "\n")),
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
    ("token not found", lambda: (_ for _ in ()).throw(TokenNotFound(SECRET))),
    ("already exists", lambda: (_ for _ in ()).throw(AlreadyExists("token", SECRET))),
    ("section not found", lambda: (_ for _ in ()).throw(SectionNotFound(NoteId(SLUG_SECRET), SECRET))),
]


@pytest.mark.parametrize("build", [b for _, b in RAISING], ids=[n for n, _ in RAISING])
def test_exceptions_never_carry_the_received_value(build: Callable[[], Any]) -> None:
    with pytest.raises(Exception) as excinfo:  # noqa: PT011
        build()
    _assert_clean(*_exception_texts(excinfo.value))


_FIELDS = ("id", "title", "type", "area", "priority", "sensitivity", "status", "summary")
_VALUES: tuple[object, ...] = (SECRET, SLUG_SECRET, 123, True, [SECRET], {SECRET: 1})
# Valid field values: they parse, so there is no report to inspect.
_ACCEPTED: tuple[tuple[str, object], ...] = (
    *((field, SLUG_SECRET) for field in ("id", "title", "type", "area", "summary")),
    *((field, SECRET) for field in ("title", "summary")),
)


@pytest.mark.parametrize(("field", "value"), [(f, v) for f in _FIELDS for v in _VALUES if (f, v) not in _ACCEPTED])
def test_frontmatter_reports_never_carry_the_received_value(field: str, value: object) -> None:
    report = parse_note(make_raw(**{field: value}), "body")
    assert isinstance(report, ValidationReport)
    _assert_clean(*(v.message for v in report.violations), *(v.field for v in report.violations))


def test_frontmatter_unknown_and_date_fields_never_echo() -> None:
    for raw in (
        make_raw(**{SECRET: "x", SLUG_SECRET: "y"}),
        make_raw(created=SECRET, updated=SLUG_SECRET, review_by=SECRET, related=SECRET, hub=[SECRET]),
        {SECRET: 1, 7: 2},
    ):
        report = parse_note(raw, "body")
        assert isinstance(report, ValidationReport)
        _assert_clean(*(v.message for v in report.violations), *(v.field for v in report.violations))
    for raw_value in ([SECRET], SECRET):
        report = parse_note(raw_value, "body")
        assert isinstance(report, ValidationReport)
        _assert_clean(*(v.message for v in report.violations), *(v.field for v in report.violations))


def test_validation_findings_never_carry_received_ids_or_vocabulary() -> None:
    note = make_note(
        type=NoteType(SLUG_SECRET),
        area=Area(SLUG_SECRET),
        hub=NoteId(SLUG_SECRET),
        related=(NoteId(SLUG_SECRET + "-a"),),
        superseded_by=NoteId(SLUG_SECRET + "-b"),
        body=f"see [[{SLUG_SECRET}-c]] and [[{SLUG_SECRET}-d#Part]]",
    )
    report = validate_note(note, VOCABULARY, AGENT_WRITE_POLICY)
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


def _hub_world() -> World:
    world = World()
    world.add(
        id=NoteId("sk-secret-value-hub1"), type=HUB, priority=Priority.P1, hub=None, sensitivity=Sensitivity.RESTRICTED
    )
    for number, parent in ((2, 1), (3, 2), (4, 3)):
        world.add(
            id=NoteId(f"sk-secret-value-hub{number}"),
            type=HUB,
            priority=Priority.P1,
            hub=NoteId(f"sk-secret-value-hub{parent}"),
            sensitivity=Sensitivity.RESTRICTED,
        )
    world.add(id=NoteId("sk-secret-value-loop-a"), hub=NoteId("sk-secret-value-loop-b"), type=HUB, priority=Priority.P1)
    world.add(id=NoteId("sk-secret-value-loop-b"), hub=NoteId("sk-secret-value-loop-a"), type=HUB, priority=Priority.P1)
    world.add(
        id=NoteId("sk-secret-value-sup-a"), superseded_by=NoteId("sk-secret-value-sup-b"), status=Status.SUPERSEDED
    )
    world.add(
        id=NoteId("sk-secret-value-sup-b"), superseded_by=NoteId("sk-secret-value-sup-a"), status=Status.SUPERSEDED
    )
    return world


def test_health_report_messages_never_name_ids_of_chains_or_targets() -> None:
    world = _hub_world()
    clock = FixedClock()
    report = HealthReport(world.notes, world.search, world.graph, clock, VOCABULARY).execute(
        HealthReportRequest(), ctx(Sensitivity.RESTRICTED, owner=True)
    )
    codes = {i.code for i in report.issues}
    assert {ViolationCode.HUB_CHAIN_TOO_DEEP, ViolationCode.HUB_CHAIN_CYCLE, ViolationCode.SUPERSEDED_BY_CYCLE} <= codes
    _assert_clean(*(i.message for i in report.issues), *(i.field for i in report.issues))


def test_secret_in_a_list_value_is_flagged_not_quoted() -> None:
    token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    report = parse_note(make_raw(related=[token]), "body")
    assert isinstance(report, ValidationReport)
    assert ViolationCode.SECRET_DETECTED in {v.code for v in report.violations}
    assert token not in repr(report)


def test_positions_replace_names() -> None:
    note = make_note(
        hub=None,
        related=(NoteId("one-note"), NoteId("two-note")),
        body="[[first-link]] and [[second-link]]",
        superseded_by=None,
    )
    messages = sorted(f.message for f in cross_note_findings(NoteRef.from_note(note), {}))
    assert messages == [
        "link #1 of body points to a note that does not exist",
        "link #2 of body points to a note that does not exist",
        "related entry #1 points to a note that does not exist",
        "related entry #2 points to a note that does not exist",
    ]


def test_authentication_failure_keeps_no_secret_in_its_chain() -> None:
    from cerebro.application.dto import AuthenticateTokenRequest
    from cerebro.application.use_cases import AuthenticateToken
    from tests.fakes.stores import InMemoryTokenStore

    use_case = AuthenticateToken(InMemoryTokenStore(), FixedClock())
    for secret in (SECRET + "\ud800", SECRET):
        with pytest.raises(Exception) as excinfo:  # noqa: PT011
            use_case.execute(AuthenticateTokenRequest(secret=secret, listener=AccessCeiling(Sensitivity.RESTRICTED)))
        _assert_clean(*_exception_texts(excinfo.value))
        assert excinfo.value.__context__ is None
