from datetime import date, datetime

import pytest

from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.frontmatter import FIELD_ORDER, note_to_raw, parse_note
from cerebro.domain.note import Note, NoteId
from cerebro.domain.report import ValidationReport, ViolationCode
from cerebro.domain.vocabulary import Area, NoteType
from tests.unit.domain.builders import make_note, make_raw

BODY = "# SSH access\n\nUse the workstation key.\n"


def parse_ok(raw: dict[str, object]) -> Note:
    result = parse_note(raw, BODY)
    assert isinstance(result, Note), result
    return result


def parse_fail(raw: dict[str, object]) -> ValidationReport:
    result = parse_note(raw, BODY)
    assert isinstance(result, ValidationReport), result
    assert not result.ok
    return result


def test_parses_a_complete_frontmatter_into_a_typed_note() -> None:
    note = parse_ok(make_raw())

    assert note.id == NoteId("server-ssh-access")
    assert note.type == NoteType("runbook")
    assert note.area == Area("infrastructure")
    assert note.priority is Priority.P2
    assert note.sensitivity is Sensitivity.RESTRICTED
    assert note.status is Status.VERIFIED
    assert note.hub == NoteId("infrastructure")
    assert note.related == (NoteId("server-docker-conventions"),)
    assert note.superseded_by is None
    assert note.created == date(2026, 11, 24)
    assert note.review_by == date(2027, 5, 24)
    assert note.body == BODY


def test_optional_fields_may_be_missing() -> None:
    raw = make_raw()
    for key in ("hub", "related", "superseded_by", "lang", "review_by"):
        del raw[key]

    note = parse_ok(raw)

    assert note.hub is None
    assert note.related == ()
    assert note.superseded_by is None
    assert note.lang == "en"
    assert note.review_by is None


def test_null_optional_fields_take_defaults() -> None:
    note = parse_ok(make_raw(related=None, lang=None, review_by=None))

    assert note.related == ()
    assert note.lang == "en"


def test_accepts_iso_date_strings() -> None:
    note = parse_ok(make_raw(created="2026-11-01", updated="2026-11-24"))

    assert note.created == date(2026, 11, 1)


@pytest.mark.parametrize(
    "missing", ["id", "title", "type", "area", "priority", "sensitivity", "status", "summary", "created", "updated"]
)
def test_reports_missing_required_fields(missing: str) -> None:
    raw = make_raw()
    del raw[missing]

    report = parse_fail(raw)

    assert [(v.code, v.field) for v in report.errors] == [(ViolationCode.MISSING_FIELD, missing)]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "Bad Id"),
        ("id", 2024),
        ("title", 42),
        ("type", "Run Book"),
        ("area", ["infrastructure"]),
        ("priority", "P9"),
        ("sensitivity", "secret"),
        ("status", "done"),
        ("summary", ["a summary"]),
        ("hub", "Not A Hub"),
        ("related", "server-docker-conventions"),
        ("related", ["ok-id", "Bad Id"]),
        ("superseded_by", 3),
        ("lang", 1),
        ("lang", False),
        ("title", True),
        ("id", 2026),
        ("created", "24/11/2026"),
        ("created", "20261124"),
        ("created", "2026-02-30"),
        ("created", " 2026-11-24"),
        ("hub", {"id": "x"}),
        ("created", datetime(2026, 11, 24, 10, 0)),
        ("updated", 20261124),
        ("review_by", "soon"),
    ],
)
def test_reports_invalid_values_with_the_field_name(field: str, value: object) -> None:
    report = parse_fail(make_raw(**{field: value}))

    assert [(v.code, v.field) for v in report.errors] == [(ViolationCode.INVALID_FIELD, field)]


def test_yaml_booleans_and_numbers_suggest_quoting() -> None:
    report = parse_fail(make_raw(lang=False))

    assert "quote the value" in report.errors[0].message


@pytest.mark.parametrize("raw", [None, ["id", "x"], "id: x"])
def test_reports_frontmatter_that_is_not_a_mapping(raw: object) -> None:
    result = parse_note(raw, BODY)

    assert isinstance(result, ValidationReport)
    assert (ViolationCode.INVALID_FIELD, None) in [(v.code, v.field) for v in result.errors]


def test_related_may_be_a_tuple() -> None:
    assert parse_ok(make_raw(related=("a", "b"))).related == (NoteId("a"), NoteId("b"))


def test_empty_related_list_is_empty() -> None:
    assert parse_ok(make_raw(related=[])).related == ()


def test_structural_problems_are_always_errors() -> None:
    report = parse_fail(make_raw(priority="P9"))

    assert report.warnings == ()


def test_null_required_fields_count_as_missing() -> None:
    report = parse_fail(make_raw(summary=None))

    assert [(v.code, v.field) for v in report.errors] == [(ViolationCode.MISSING_FIELD, "summary")]


def test_reports_unknown_fields() -> None:
    report = parse_fail(make_raw(tags=["x"], Title="dup"))

    assert sorted((v.code, v.field) for v in report.errors) == [
        (ViolationCode.UNKNOWN_FIELD, "Title"),
        (ViolationCode.UNKNOWN_FIELD, "tags"),
    ]


def test_reports_non_string_keys() -> None:
    raw: dict[object, object] = dict(make_raw().items())
    raw[1] = "x"

    report = parse_fail(raw)  # type: ignore[arg-type]

    assert [(v.code, v.field) for v in report.errors] == [(ViolationCode.UNKNOWN_FIELD, "1")]


def test_accumulates_every_problem_at_once() -> None:
    raw = make_raw(priority="P9", status="done", extra=True)
    del raw["title"]

    report = parse_fail(raw)

    assert {v.field for v in report.errors} == {"priority", "status", "extra", "title"}


def test_body_must_be_a_string() -> None:
    result = parse_note(make_raw(), None)

    assert isinstance(result, ValidationReport)
    assert [(v.code, v.field) for v in result.errors] == [(ViolationCode.INVALID_FIELD, "body")]


class TestNoteToRaw:
    def test_uses_canonical_field_order(self) -> None:
        raw = note_to_raw(make_note(review_by=date(2027, 5, 24), superseded_by=NoteId("other")))

        assert list(raw) == list(FIELD_ORDER)

    def test_omits_empty_optional_fields(self) -> None:
        raw = note_to_raw(make_note(hub=None, related=(), superseded_by=None, review_by=None))

        assert "hub" not in raw
        assert "related" not in raw
        assert "superseded_by" not in raw
        assert "review_by" not in raw
        assert raw["lang"] == "en"

    def test_contains_only_plain_data(self) -> None:
        raw = note_to_raw(make_note())

        assert raw["id"] == "server-ssh-access"
        assert raw["priority"] == "P2"
        assert type(raw["priority"]) is str
        assert raw["related"] == ["server-docker-conventions"]
        assert raw["created"] == date(2026, 11, 24)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"review_by": date(2027, 5, 24), "related": (NoteId("a"), NoteId("b"))},
            {"status": Status.SUPERSEDED, "superseded_by": NoteId("server-ssh-v2"), "hub": None},
            {"type": NoteType("core"), "priority": Priority.P0, "hub": None, "related": ()},
            {"type": NoteType("hub"), "priority": Priority.P1, "hub": NoteId("parent-hub")},
            {"lang": "", "title": ""},
        ],
    )
    def test_round_trips_through_parse(self, overrides: dict[str, object]) -> None:
        note = make_note(**overrides)

        assert parse_note(note_to_raw(note), note.body) == note
