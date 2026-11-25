from datetime import date

import pytest

from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.note import NoteId
from cerebro.domain.report import Severity, ViolationCode
from cerebro.domain.validation import validate_note
from cerebro.domain.vocabulary import Area, NoteType
from tests.unit.domain.builders import VOCABULARY, make_note, words


def codes(**overrides: object) -> list[ViolationCode]:
    return [v.code for v in validate_note(make_note(**overrides), VOCABULARY).violations]


def test_a_valid_note_has_no_violations() -> None:
    assert validate_note(make_note(), VOCABULARY).violations == ()


class TestTitle:
    @pytest.mark.parametrize("title", ["", "   "])
    def test_must_not_be_blank(self, title: str) -> None:
        assert codes(title=title) == [ViolationCode.TITLE_BLANK]

    def test_is_limited_to_120_characters(self) -> None:
        assert codes(title="x" * 120) == []
        assert codes(title="x" * 121) == [ViolationCode.TITLE_TOO_LONG]


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

    def test_is_not_required_for_core_notes(self) -> None:
        assert codes(type=NoteType("core"), priority=Priority.P0, sensitivity=Sensitivity.INTERNAL, hub=None) == []

    def test_is_optional_for_hubs(self) -> None:
        assert codes(type=NoteType("hub"), priority=Priority.P1, hub=None) == []
        assert codes(id=NoteId("infra-docker"), type=NoteType("hub"), priority=Priority.P1) == []

    def test_cannot_point_to_the_note_itself(self) -> None:
        assert codes(hub=NoteId("server-ssh-access")) == [ViolationCode.SELF_REFERENCE]


class TestPriorityAndType:
    def test_core_type_requires_p0(self) -> None:
        assert codes(type=NoteType("core")) == [ViolationCode.PRIORITY_TYPE_MISMATCH]

    def test_p0_requires_core_type(self) -> None:
        assert codes(priority=Priority.P0, sensitivity=Sensitivity.INTERNAL) == [ViolationCode.PRIORITY_TYPE_MISMATCH]

    def test_hub_type_requires_p1(self) -> None:
        assert codes(type=NoteType("hub")) == [ViolationCode.PRIORITY_TYPE_MISMATCH]

    def test_p1_requires_hub_type(self) -> None:
        assert codes(priority=Priority.P1) == [ViolationCode.PRIORITY_TYPE_MISMATCH]

    def test_core_notes_cannot_be_restricted(self) -> None:
        assert codes(type=NoteType("core"), priority=Priority.P0, hub=None) == [ViolationCode.CORE_RESTRICTED]


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

    def test_duplicates_are_a_warning(self) -> None:
        report = validate_note(make_note(related=(NoteId("a"), NoteId("a"))), VOCABULARY)

        assert report.ok
        assert [(v.code, v.severity) for v in report.violations] == [
            (ViolationCode.DUPLICATE_RELATED, Severity.WARNING)
        ]


class TestDates:
    def test_updated_must_not_be_before_created(self) -> None:
        assert codes(created=date(2026, 11, 24), updated=date(2026, 11, 23)) == [ViolationCode.DATES_OUT_OF_ORDER]

    def test_updated_may_equal_or_follow_created(self) -> None:
        assert codes(created=date(2026, 11, 1), updated=date(2026, 11, 24)) == []


def test_includes_the_body_budget() -> None:
    assert codes(body=words(3001)) == [ViolationCode.BODY_OVER_HARD_LIMIT]


def test_reports_every_problem_at_once() -> None:
    found = set(codes(title="", summary="short", hub=None, area=Area("cooking")))

    assert found == {
        ViolationCode.TITLE_BLANK,
        ViolationCode.SUMMARY_LENGTH,
        ViolationCode.HUB_REQUIRED,
        ViolationCode.UNKNOWN_AREA,
    }
