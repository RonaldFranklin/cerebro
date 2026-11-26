"""Allowlisted owner write outcomes, including validation suitable for rejected input."""

from cerebro.application.dto import ApplyWarning
from cerebro.domain.frontmatter import FIELD_ORDER
from cerebro.domain.report import ValidationReport

_FIELDS = frozenset(FIELD_ORDER) | {"body"}


def validation_view(report: ValidationReport) -> dict[str, object]:
    return {
        "ok": report.ok,
        "violations": [
            {
                "code": violation.code.value,
                "field": violation.field if violation.field in _FIELDS else None,
                "severity": violation.severity.value,
                "line": violation.line,
            }
            for violation in report.violations
        ],
    }


def warnings_view(warnings: tuple[ApplyWarning, ...]) -> list[dict[str, str]]:
    return [{"code": item.code.value, "message": item.message} for item in warnings]
