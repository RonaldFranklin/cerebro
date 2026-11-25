import pytest

from cerebro.domain.report import Severity, ValidationReport, Violation, ViolationCode

ERROR = Violation(code=ViolationCode.SUMMARY_LENGTH, field="summary", message="too short", severity=Severity.ERROR)
WARNING = Violation(code=ViolationCode.BODY_OVER_SOFT_LIMIT, field="body", message="long", severity=Severity.WARNING)


def test_empty_report_is_ok() -> None:
    report = ValidationReport()

    assert report.ok
    assert report.errors == ()
    assert report.warnings == ()


def test_warnings_do_not_make_a_report_fail() -> None:
    report = ValidationReport(violations=(WARNING,))

    assert report.ok
    assert report.warnings == (WARNING,)


def test_errors_make_a_report_fail() -> None:
    report = ValidationReport(violations=(WARNING, ERROR))

    assert not report.ok
    assert report.errors == (ERROR,)


def test_reports_combine_in_order() -> None:
    combined = ValidationReport(violations=(ERROR,)) + ValidationReport(violations=(WARNING,))

    assert combined.violations == (ERROR, WARNING)


def test_codes_lists_the_violation_codes() -> None:
    assert ValidationReport(violations=(ERROR, WARNING)).codes == frozenset(
        {ViolationCode.SUMMARY_LENGTH, ViolationCode.BODY_OVER_SOFT_LIMIT}
    )


def test_violations_must_be_a_tuple() -> None:
    with pytest.raises(TypeError):
        ValidationReport(violations=[ERROR])  # type: ignore[arg-type]
