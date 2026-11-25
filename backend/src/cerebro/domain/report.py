"""Validation results: every rule reports violations instead of raising, so all problems surface at once."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Self


class Severity(StrEnum):
    """``error`` blocks a change; ``warning`` is reported (e.g. by the health report) but does not block."""

    ERROR = "error"
    WARNING = "warning"


class ViolationCode(StrEnum):
    """Stable machine-readable codes; adapters and the UI may rely on them."""

    # frontmatter structure
    MISSING_FIELD = "missing_field"
    INVALID_FIELD = "invalid_field"
    UNKNOWN_FIELD = "unknown_field"
    # field rules
    TITLE_BLANK = "title_blank"
    TITLE_TOO_LONG = "title_too_long"
    UNKNOWN_TYPE = "unknown_type"
    UNKNOWN_AREA = "unknown_area"
    SUMMARY_LENGTH = "summary_length"
    HUB_REQUIRED = "hub_required"
    PRIORITY_TYPE_MISMATCH = "priority_type_mismatch"
    CORE_RESTRICTED = "core_restricted"
    SUPERSEDED_BY_REQUIRED = "superseded_by_required"
    SUPERSEDED_BY_UNEXPECTED = "superseded_by_unexpected"
    SELF_REFERENCE = "self_reference"
    DUPLICATE_RELATED = "duplicate_related"
    DATES_OUT_OF_ORDER = "dates_out_of_order"
    # budgets
    BODY_OVER_SOFT_LIMIT = "body_over_soft_limit"
    BODY_OVER_HARD_LIMIT = "body_over_hard_limit"
    HUB_OVER_BUDGET = "hub_over_budget"
    CORE_OVER_BUDGET = "core_over_budget"


@dataclass(frozen=True, slots=True, kw_only=True)
class Violation:
    code: ViolationCode
    field: str | None
    message: str
    severity: Severity = Severity.ERROR


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
