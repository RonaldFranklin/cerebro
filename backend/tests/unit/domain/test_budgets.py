import pytest

from cerebro.domain.budgets import (
    CORE_BUDGET_WORDS,
    HUB_BUDGET_WORDS,
    NOTE_HARD_LIMIT_WORDS,
    NOTE_SOFT_LIMIT_WORDS,
    body_budget_findings,
    core_budget_findings,
    word_count,
)
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.note import NoteId
from cerebro.domain.report import ViolationCode
from cerebro.domain.vocabulary import NoteType
from tests.unit.domain.builders import make_note, words


def budget_codes(**overrides: object) -> list[ViolationCode]:
    return [f.code for f in body_budget_findings(make_note(**overrides))]


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


def test_budget_constants_match_context_limits() -> None:
    assert (CORE_BUDGET_WORDS, HUB_BUDGET_WORDS, NOTE_SOFT_LIMIT_WORDS, NOTE_HARD_LIMIT_WORDS) == (
        800,
        800,
        1500,
        3000,
    )


class TestBodyBudget:
    @pytest.mark.parametrize("priority", [Priority.P2, Priority.P3])
    def test_regular_note_within_soft_limit_is_fine(self, priority: Priority) -> None:
        assert budget_codes(priority=priority, body=words(1500)) == []

    def test_regular_note_above_soft_limit_is_flagged(self) -> None:
        assert budget_codes(body=words(1501)) == [ViolationCode.BODY_OVER_SOFT_LIMIT]

    def test_regular_note_at_hard_limit_is_only_over_the_soft_limit(self) -> None:
        assert budget_codes(body=words(3000)) == [ViolationCode.BODY_OVER_SOFT_LIMIT]

    def test_regular_note_above_hard_limit(self) -> None:
        assert budget_codes(priority=Priority.P3, body=words(3001)) == [ViolationCode.BODY_OVER_HARD_LIMIT]

    def test_hub_introduction_is_limited_to_800_words(self) -> None:
        assert budget_codes(type=NoteType("hub"), priority=Priority.P1, body=words(800)) == []
        assert budget_codes(type=NoteType("hub"), priority=Priority.P1, body=words(801)) == [
            ViolationCode.HUB_OVER_BUDGET
        ]

    def test_hub_budget_follows_the_type_even_with_a_wrong_priority(self) -> None:
        assert budget_codes(type=NoteType("hub"), priority=Priority.P0, body=words(801)) == [
            ViolationCode.HUB_OVER_BUDGET
        ]

    def test_core_notes_have_no_individual_limit(self) -> None:
        core = {"type": NoteType("core"), "priority": Priority.P0, "sensitivity": Sensitivity.INTERNAL}

        assert budget_codes(**core, hub=None, body=words(3001)) == []
        assert budget_codes(type=NoteType("core"), priority=Priority.P2, body=words(3001)) == []


class TestCoreBudget:
    def test_all_core_notes_together_may_use_800_words(self) -> None:
        assert core_budget_findings({NoteId("a"): 500, NoteId("b"): 300}) == ()

    def test_more_than_800_core_words_is_flagged(self) -> None:
        findings = core_budget_findings({NoteId("a"): 500, NoteId("b"): 301})

        assert [(f.code, f.field) for f in findings] == [(ViolationCode.CORE_OVER_BUDGET, None)]
        assert "801" in findings[0].message

    def test_no_core_notes_is_fine(self) -> None:
        assert core_budget_findings({}) == ()
