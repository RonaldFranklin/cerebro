"""Context budgets (ADR-0004): word limits per note and for all P0 notes together."""

import re
from collections.abc import Mapping

from cerebro.domain.note import Note, NoteId
from cerebro.domain.report import Finding, ViolationCode
from cerebro.domain.vocabulary import CORE

CORE_BUDGET_WORDS = 800
"""All current P0 notes together."""
HUB_BUDGET_WORDS = 800
"""Each hub introduction (ADR-0015)."""
NOTE_SOFT_LIMIT_WORDS = 1500
"""Other notes: above this the note is flagged."""
NOTE_HARD_LIMIT_WORDS = 3000
"""Other notes: above this the note is rejected."""

_WORD = re.compile(r"\S+")


def word_count(text: str) -> int:
    """Count whitespace-separated tokens. Markdown syntax and code blocks count like any other text."""
    return len(_WORD.findall(text))


def body_budget_findings(note: Note) -> tuple[Finding, ...]:
    """Check one note's body against the limit for its type. Core notes are only limited together."""
    words = word_count(note.body)
    if note.type == CORE:
        return ()
    if note.is_hub:
        return _over(ViolationCode.HUB_OVER_BUDGET, words, HUB_BUDGET_WORDS, "body")
    if words > NOTE_HARD_LIMIT_WORDS:
        return _over(ViolationCode.BODY_OVER_HARD_LIMIT, words, NOTE_HARD_LIMIT_WORDS, "body")
    return _over(ViolationCode.BODY_OVER_SOFT_LIMIT, words, NOTE_SOFT_LIMIT_WORDS, "body")


def core_budget_findings(core_word_counts: Mapping[NoteId, int]) -> tuple[Finding, ...]:
    """Check the combined size of the P0 notes served by ``GetOverview``.

    The caller passes the word count of every **current** P0 note (superseded/archived ones are never served),
    with the proposed version substituted when validating a change.
    """
    return _over(ViolationCode.CORE_OVER_BUDGET, sum(core_word_counts.values()), CORE_BUDGET_WORDS, None)


def _over(code: ViolationCode, words: int, limit: int, field: str | None) -> tuple[Finding, ...]:
    if words <= limit:
        return ()
    return (Finding(code=code, field=field, message=f"{words} words exceeds the limit of {limit}"),)
