"""Context budgets (ADR-0004): word limits per note and for all P0 notes together."""

import re
from collections.abc import Mapping

from cerebro.domain.enums import Priority
from cerebro.domain.note import Note, NoteId
from cerebro.domain.report import Severity, ValidationReport, Violation, ViolationCode

CORE_BUDGET_WORDS = 1500
"""All P0 notes together."""
HUB_BUDGET_WORDS = 800
"""Each hub introduction (ADR-0015)."""
NOTE_SOFT_LIMIT_WORDS = 1500
"""P2/P3 notes: above this the health report warns."""
NOTE_HARD_LIMIT_WORDS = 3000
"""P2/P3 notes: above this the note is rejected."""

_WORD = re.compile(r"\S+")


def word_count(text: str) -> int:
    """Count whitespace-separated tokens. Markdown syntax and code blocks count like any other text."""
    return len(_WORD.findall(text))


def body_budget_report(note: Note) -> ValidationReport:
    """Check the body of one note against its own limit. P0 notes are only limited together."""
    words = word_count(note.body)
    if note.priority is Priority.P0:
        return ValidationReport()
    if note.is_hub:
        if words > HUB_BUDGET_WORDS:
            return _report(ViolationCode.HUB_OVER_BUDGET, words, HUB_BUDGET_WORDS, Severity.ERROR, "body")
        return ValidationReport()
    if words > NOTE_HARD_LIMIT_WORDS:
        return _report(ViolationCode.BODY_OVER_HARD_LIMIT, words, NOTE_HARD_LIMIT_WORDS, Severity.ERROR, "body")
    if words > NOTE_SOFT_LIMIT_WORDS:
        return _report(ViolationCode.BODY_OVER_SOFT_LIMIT, words, NOTE_SOFT_LIMIT_WORDS, Severity.WARNING, "body")
    return ValidationReport()


def core_budget_report(core_word_counts: Mapping[NoteId, int]) -> ValidationReport:
    """Check the combined size of all P0 notes, given the word count of each."""
    total = sum(core_word_counts.values())
    if total > CORE_BUDGET_WORDS:
        return _report(ViolationCode.CORE_OVER_BUDGET, total, CORE_BUDGET_WORDS, Severity.ERROR, None)
    return ValidationReport()


def _report(code: ViolationCode, words: int, limit: int, severity: Severity, field: str | None) -> ValidationReport:
    return ValidationReport(
        (Violation(code=code, field=field, message=f"{words} words exceeds the limit of {limit}", severity=severity),)
    )
