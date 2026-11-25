import pytest

from cerebro.domain.budgets import (
    CORE_BUDGET_WORDS,
    HUB_BUDGET_WORDS,
    NOTE_HARD_LIMIT_WORDS,
    NOTE_SOFT_LIMIT_WORDS,
    body_budget_report,
    core_budget_report,
    word_count,
)
from cerebro.domain.enums import Priority
from cerebro.domain.note import NoteId
from cerebro.domain.report import Severity, ViolationCode
from cerebro.domain.vocabulary import NoteType
from tests.unit.domain.builders import make_note, words


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("   \n\t ", 0),
        ("one", 1),
        ("one two\nthree\tfour", 4),
        ("# Heading\n\n- item `code`", 5),
        ("```\nprint('x')\n```", 3),
    ],
)
def test_word_count_counts_whitespace_separated_tokens(text: str, expected: int) -> None:
    assert word_count(text) == expected


def test_budget_constants_match_adr_0004() -> None:
    assert (CORE_BUDGET_WORDS, HUB_BUDGET_WORDS, NOTE_SOFT_LIMIT_WORDS, NOTE_HARD_LIMIT_WORDS) == (
        1500,
        800,
        1500,
        3000,
    )


class TestBodyBudget:
    @pytest.mark.parametrize("priority", [Priority.P2, Priority.P3])
    def test_regular_note_within_soft_limit_is_fine(self, priority: Priority) -> None:
        assert body_budget_report(make_note(priority=priority, body=words(1500))).violations == ()

    def test_regular_note_above_soft_limit_is_a_warning(self) -> None:
        report = body_budget_report(make_note(body=words(1501)))

        assert report.ok
        assert [(v.code, v.severity) for v in report.violations] == [
            (ViolationCode.BODY_OVER_SOFT_LIMIT, Severity.WARNING)
        ]

    def test_regular_note_above_hard_limit_is_an_error(self) -> None:
        report = body_budget_report(make_note(priority=Priority.P3, body=words(3001)))

        assert [(v.code, v.severity) for v in report.violations] == [
            (ViolationCode.BODY_OVER_HARD_LIMIT, Severity.ERROR)
        ]

    def test_hub_introduction_is_limited_to_800_words(self) -> None:
        assert body_budget_report(make_note(type=NoteType("hub"), priority=Priority.P1, body=words(800))).ok
        report = body_budget_report(make_note(type=NoteType("hub"), priority=Priority.P1, body=words(801)))
        assert [(v.code, v.severity) for v in report.violations] == [(ViolationCode.HUB_OVER_BUDGET, Severity.ERROR)]

    def test_core_notes_have_no_individual_limit(self) -> None:
        core = make_note(type=NoteType("core"), priority=Priority.P0, hub=None, body=words(1600))

        assert body_budget_report(core).violations == ()


class TestCoreBudget:
    def test_all_core_notes_together_may_use_1500_words(self) -> None:
        assert core_budget_report({NoteId("a"): 1000, NoteId("b"): 500}).ok

    def test_more_than_1500_core_words_is_an_error(self) -> None:
        report = core_budget_report({NoteId("a"): 1000, NoteId("b"): 501})

        assert [(v.code, v.severity, v.field) for v in report.violations] == [
            (ViolationCode.CORE_OVER_BUDGET, Severity.ERROR, None)
        ]
        assert "1501" in report.violations[0].message

    def test_no_core_notes_is_fine(self) -> None:
        assert core_budget_report({}).ok
