"""Validation results.

Rules never raise and never decide severity: they return ``Finding``s (code, field, message). A
``SeverityPolicy`` chosen by the write path turns findings into a ``ValidationReport`` where each
``Violation`` is an ``error`` (blocks the change) or a ``warning`` (reported, does not block). The same
rule can therefore be an error for agent proposals and a warning for owner edits (e.g. broken links).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Self


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ViolationCode(StrEnum):
    """Stable machine-readable codes; adapters and the UI may rely on them."""

    # frontmatter structure (always errors: no note can be built)
    MISSING_FIELD = "missing_field"
    INVALID_FIELD = "invalid_field"
    UNKNOWN_FIELD = "unknown_field"
    # field rules
    TITLE_BLANK = "title_blank"
    TITLE_TOO_LONG = "title_too_long"
    TITLE_MULTILINE = "title_multiline"
    LANG_BLANK = "lang_blank"
    UNKNOWN_TYPE = "unknown_type"
    UNKNOWN_AREA = "unknown_area"
    SUMMARY_LENGTH = "summary_length"
    HUB_REQUIRED = "hub_required"
    HUB_NOT_ALLOWED = "hub_not_allowed"
    PRIORITY_TYPE_MISMATCH = "priority_type_mismatch"
    CORE_RESTRICTED = "core_restricted"
    SUPERSEDED_BY_REQUIRED = "superseded_by_required"
    SUPERSEDED_BY_UNEXPECTED = "superseded_by_unexpected"
    SELF_REFERENCE = "self_reference"
    DUPLICATE_RELATED = "duplicate_related"
    DATES_OUT_OF_ORDER = "dates_out_of_order"
    # secrets (secrets.py)
    SECRET_DETECTED = "secret_detected"
    # cross-note rules (links.py / cross_note.py)
    BROKEN_LINK = "broken_link"
    HUB_NOT_FOUND = "hub_not_found"
    HUB_NOT_A_HUB = "hub_not_a_hub"
    SUPERSEDED_BY_CYCLE = "superseded_by_cycle"
    HUB_CHAIN_CYCLE = "hub_chain_cycle"
    HUB_CHAIN_TOO_DEEP = "hub_chain_too_deep"
    # budgets
    BODY_OVER_SOFT_LIMIT = "body_over_soft_limit"
    BODY_OVER_HARD_LIMIT = "body_over_hard_limit"
    HUB_OVER_BUDGET = "hub_over_budget"
    CORE_OVER_BUDGET = "core_over_budget"


@dataclass(frozen=True, slots=True, kw_only=True)
class Finding:
    """A rule that does not hold, before a severity is assigned."""

    code: ViolationCode
    field: str | None
    message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Violation:
    code: ViolationCode
    field: str | None
    message: str
    severity: Severity


@dataclass(frozen=True, slots=True)
class ValidationReport:
    violations: tuple[Violation, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.violations, tuple):
            raise TypeError("violations must be a tuple")

    @property
    def errors(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity is Severity.WARNING)

    @property
    def ok(self) -> bool:
        """True when there are no errors (warnings are allowed)."""
        return not self.errors

    @property
    def codes(self) -> frozenset[ViolationCode]:
        return frozenset(v.code for v in self.violations)

    def __add__(self, other: Self) -> Self:
        return type(self)(self.violations + other.violations)


@dataclass(frozen=True, slots=True, kw_only=True)
class SeverityPolicy:
    """Which findings only warn on a given write path; every other finding is an error."""

    name: str
    warnings: frozenset[ViolationCode]

    def severity(self, code: ViolationCode) -> Severity:
        return Severity.WARNING if code in self.warnings else Severity.ERROR

    def report(self, findings: Iterable[Finding]) -> ValidationReport:
        return ValidationReport(
            tuple(
                Violation(code=f.code, field=f.field, message=f.message, severity=self.severity(f.code))
                for f in findings
            )
        )


_ALWAYS_WARNINGS: Final = frozenset({ViolationCode.BODY_OVER_SOFT_LIMIT, ViolationCode.DUPLICATE_RELATED})

PROPOSAL_POLICY: Final = SeverityPolicy(name="proposal", warnings=_ALWAYS_WARNINGS)
"""Agent proposals: strict."""

OWNER_EDIT_POLICY: Final = SeverityPolicy(name="owner_edit", warnings=_ALWAYS_WARNINGS | {ViolationCode.BROKEN_LINK})
"""Owner edits and health reports: broken links only warn (the owner may link to a note not written yet)."""
