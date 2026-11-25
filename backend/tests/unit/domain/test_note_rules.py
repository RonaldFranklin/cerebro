from datetime import date

import pytest

from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.note import NoteId
from cerebro.domain.report import OWNER_EDIT_POLICY, PROPOSAL_POLICY, Severity, SeverityPolicy, ViolationCode
from cerebro.domain.validation import allowed_priorities, validate_note
from cerebro.domain.vocabulary import Area, NoteType
from tests.unit.domain.builders import VOCABULARY, make_note, words

CORE = {"type": NoteType("core"), "priority": Priority.P0, "sensitivity": Sensitivity.INTERNAL, "hub": None}
HUB = {"type": NoteType("hub"), "priority": Priority.P1, "hub": None}


def codes(**overrides: object) -> list[ViolationCode]:
    return [v.code for v in validate_note(make_note(**overrides), VOCABULARY, PROPOSAL_POLICY).violations]


def test_a_valid_note_has_no_violations() -> None:
    assert validate_note(make_note(), VOCABULARY, PROPOSAL_POLICY).violations == ()


@pytest.mark.parametrize("overrides", [CORE, HUB, {**HUB, "hub": NoteId("infrastructure")}])
def test_valid_core_and_hub_notes(overrides: dict[str, object]) -> None:
    assert codes(**overrides) == []


class TestTitle:
    @pytest.mark.parametrize("title", ["", "   "])
    def test_must_not_be_blank(self, title: str) -> None:
        assert codes(title=title) == [ViolationCode.TITLE_BLANK]

    def test_is_limited_to_120_characters_after_trimming(self) -> None:
        assert codes(title="x" * 120) == []
        assert codes(title="  " + "x" * 120 + "  ") == []
        assert codes(title="x" * 121) == [ViolationCode.TITLE_TOO_LONG]

    @pytest.mark.parametrize("title", ["first\nsecond", "first\r\nsecond"])
    def test_must_be_a_single_line(self, title: str) -> None:
        assert codes(title=title) == [ViolationCode.TITLE_MULTILINE]


@pytest.mark.parametrize("lang", ["", "  "])
def test_lang_must_not_be_blank(lang: str) -> None:
    assert codes(lang=lang) == [ViolationCode.LANG_BLANK]


class TestVocabulary:
    def test_type_must_be_allowed(self) -> None:
        assert codes(type=NoteType("recipe")) == [ViolationCode.UNKNOWN_TYPE]

    def test_area_must_be_allowed(self) -> None:
        assert codes(area=Area("cooking")) == [ViolationCode.UNKNOWN_AREA]


class TestSummary:
    @pytest.mark.parametrize("length", [40, 300])
    def test_accepts_40_to_300_characters(self, length: int) -> None:
        assert codes(summary="s" * length) == []

    @pytest.mark.parametrize("length", [0, 39, 301])
    def test_rejects_other_lengths(self, length: int) -> None:
        assert codes(summary="s" * length) == [ViolationCode.SUMMARY_LENGTH]

    def test_surrounding_whitespace_does_not_count(self) -> None:
        assert codes(summary="  " + "s" * 39 + "  ") == [ViolationCode.SUMMARY_LENGTH]


class TestHubField:
    def test_is_required_for_regular_notes(self) -> None:
        assert codes(hub=None) == [ViolationCode.HUB_REQUIRED]

    def test_is_not_allowed_for_core_notes(self) -> None:
        assert codes(**{**CORE, "hub": NoteId("infrastructure")}) == [ViolationCode.HUB_NOT_ALLOWED]

    def test_cannot_point_to_the_note_itself(self) -> None:
        assert codes(hub=NoteId("server-ssh-access")) == [ViolationCode.SELF_REFERENCE]


class TestPriorityFollowsType:
    @pytest.mark.parametrize(
        ("note_type", "expected"),
        [
            ("core", {Priority.P0}),
            ("hub", {Priority.P1}),
            ("runbook", {Priority.P2, Priority.P3}),
            ("concept", {Priority.P2, Priority.P3}),
        ],
    )
    def test_allowed_priorities(self, note_type: str, expected: set[Priority]) -> None:
        assert allowed_priorities(NoteType(note_type)) == expected

    @pytest.mark.parametrize(
        "overrides",
        [
            {**CORE, "priority": Priority.P2},
            {**CORE, "priority": Priority.P1},
            {**HUB, "priority": Priority.P0, "sensitivity": Sensitivity.INTERNAL},
            {**HUB, "priority": Priority.P3},
            {"priority": Priority.P1},
        ],
    )
    def test_one_violation_per_mismatch(self, overrides: dict[str, object]) -> None:
        report = validate_note(make_note(**overrides), VOCABULARY, PROPOSAL_POLICY)

        assert [v.code for v in report.violations] == [ViolationCode.PRIORITY_TYPE_MISMATCH]

    def test_message_names_the_expected_priority(self) -> None:
        report = validate_note(make_note(**{**HUB, "priority": Priority.P3}), VOCABULARY, PROPOSAL_POLICY)

        assert report.violations[0].message == "type hub requires priority P1"

    def test_regular_note_at_p0_is_a_mismatch_and_cannot_be_restricted(self) -> None:
        assert codes(priority=Priority.P0) == [ViolationCode.PRIORITY_TYPE_MISMATCH, ViolationCode.CORE_RESTRICTED]

    def test_core_notes_cannot_be_restricted(self) -> None:
        assert codes(**{**CORE, "sensitivity": Sensitivity.RESTRICTED}) == [ViolationCode.CORE_RESTRICTED]


class TestSupersededBy:
    def test_is_required_when_superseded(self) -> None:
        assert codes(status=Status.SUPERSEDED) == [ViolationCode.SUPERSEDED_BY_REQUIRED]

    def test_is_allowed_only_when_superseded(self) -> None:
        assert codes(superseded_by=NoteId("other")) == [ViolationCode.SUPERSEDED_BY_UNEXPECTED]

    def test_valid_pair(self) -> None:
        assert codes(status=Status.SUPERSEDED, superseded_by=NoteId("other")) == []

    def test_cannot_point_to_the_note_itself(self) -> None:
        assert codes(status=Status.SUPERSEDED, superseded_by=NoteId("server-ssh-access")) == [
            ViolationCode.SELF_REFERENCE
        ]


class TestRelated:
    def test_cannot_contain_the_note_itself(self) -> None:
        assert codes(related=(NoteId("server-ssh-access"),)) == [ViolationCode.SELF_REFERENCE]

    @pytest.mark.parametrize("policy", [PROPOSAL_POLICY, OWNER_EDIT_POLICY])
    def test_duplicates_only_warn(self, policy: SeverityPolicy) -> None:
        report = validate_note(make_note(related=(NoteId("a"), NoteId("a"))), VOCABULARY, policy)

        assert report.ok
        assert [(v.code, v.severity) for v in report.violations] == [
            (ViolationCode.DUPLICATE_RELATED, Severity.WARNING)
        ]


class TestDates:
    def test_updated_must_not_be_before_created(self) -> None:
        assert codes(created=date(2026, 11, 24), updated=date(2026, 11, 23)) == [ViolationCode.DATES_OUT_OF_ORDER]

    def test_updated_may_equal_or_follow_created(self) -> None:
        assert codes(created=date(2026, 11, 1), updated=date(2026, 11, 24)) == []


class TestBudgetSeverity:
    def test_hard_limit_is_an_error(self) -> None:
        report = validate_note(make_note(body=words(3001)), VOCABULARY, PROPOSAL_POLICY)

        assert [(v.code, v.severity) for v in report.violations] == [
            (ViolationCode.BODY_OVER_HARD_LIMIT, Severity.ERROR)
        ]

    def test_soft_limit_is_a_warning(self) -> None:
        report = validate_note(make_note(body=words(1501)), VOCABULARY, PROPOSAL_POLICY)

        assert report.ok
        assert [(v.code, v.severity) for v in report.violations] == [
            (ViolationCode.BODY_OVER_SOFT_LIMIT, Severity.WARNING)
        ]


def test_reports_every_problem_at_once() -> None:
    found = set(codes(title="", summary="short", hub=None, area=Area("cooking")))

    assert found == {
        ViolationCode.TITLE_BLANK,
        ViolationCode.SUMMARY_LENGTH,
        ViolationCode.HUB_REQUIRED,
        ViolationCode.UNKNOWN_AREA,
    }
