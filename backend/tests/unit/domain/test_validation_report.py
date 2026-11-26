import pytest

from cerebro.domain.report import (
    AGENT_WRITE_POLICY,
    OWNER_EDIT_POLICY,
    Finding,
    Severity,
    SeverityPolicy,
    ValidationReport,
    Violation,
    ViolationCode,
)

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


class TestSeverityPolicy:
    def test_assigns_warning_only_to_listed_codes(self) -> None:
        policy = SeverityPolicy(name="test", warnings=frozenset({ViolationCode.DUPLICATE_RELATED}))
        findings = [
            Finding(code=ViolationCode.DUPLICATE_RELATED, field="related", message="dup"),
            Finding(code=ViolationCode.TITLE_BLANK, field="title", message="blank"),
        ]

        report = policy.report(findings)

        assert [(v.code, v.severity) for v in report.violations] == [
            (ViolationCode.DUPLICATE_RELATED, Severity.WARNING),
            (ViolationCode.TITLE_BLANK, Severity.ERROR),
        ]
        assert report.violations[0].field == "related"
        assert report.violations[0].message == "dup"

    @pytest.mark.parametrize("policy", [AGENT_WRITE_POLICY, OWNER_EDIT_POLICY])
    def test_soft_limit_and_duplicate_related_only_warn_on_every_path(self, policy: SeverityPolicy) -> None:
        assert policy.severity(ViolationCode.BODY_OVER_SOFT_LIMIT) is Severity.WARNING
        assert policy.severity(ViolationCode.DUPLICATE_RELATED) is Severity.WARNING
        assert policy.severity(ViolationCode.BODY_OVER_HARD_LIMIT) is Severity.ERROR

    def test_empty_findings_give_an_ok_report(self) -> None:
        assert AGENT_WRITE_POLICY.report([]).violations == ()
