import pytest

from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import InvalidValueError


class TestPriority:
    def test_parses_levels(self) -> None:
        assert [Priority.parse(v) for v in ("P0", "P1", "P2", "P3")] == list(Priority)

    @pytest.mark.parametrize("value", ["p0", "P4", "", "high"])
    def test_rejects_unknown_levels(self, value: str) -> None:
        with pytest.raises(InvalidValueError) as excinfo:
            Priority.parse(value)
        assert excinfo.value.field == "priority"

    def test_p0_is_the_highest_priority(self) -> None:
        assert Priority.P0 < Priority.P1 < Priority.P2 < Priority.P3
        assert min(Priority) is Priority.P0
        assert sorted([Priority.P3, Priority.P0, Priority.P2]) == [Priority.P0, Priority.P2, Priority.P3]
        assert Priority.P1 >= Priority.P0
        assert Priority.P1 <= Priority.P1
        assert Priority.P2 > Priority.P1


class TestSensitivity:
    def test_parses_levels(self) -> None:
        assert Sensitivity.parse("internal") is Sensitivity.INTERNAL

    def test_rejects_unknown_levels(self) -> None:
        with pytest.raises(InvalidValueError) as excinfo:
            Sensitivity.parse("secret")
        assert excinfo.value.field == "sensitivity"

    def test_is_ordered_public_internal_restricted(self) -> None:
        assert Sensitivity.PUBLIC < Sensitivity.INTERNAL < Sensitivity.RESTRICTED
        assert min(Sensitivity.RESTRICTED, Sensitivity.INTERNAL) is Sensitivity.INTERNAL
        assert max(Sensitivity) is Sensitivity.RESTRICTED

    def test_does_not_compare_with_other_types(self) -> None:
        with pytest.raises(TypeError):
            _ = Sensitivity.PUBLIC < Priority.P0


class TestStatus:
    @pytest.mark.parametrize(
        ("status", "is_current"),
        [
            (Status.CURRENT, True),
            (Status.PROPOSED, True),
            (Status.APPLIED, True),
            (Status.VERIFIED, True),
            (Status.SUPERSEDED, False),
            (Status.ARCHIVED, False),
        ],
    )
    def test_only_superseded_and_archived_are_history(self, status: Status, is_current: bool) -> None:
        assert status.is_current is is_current

    def test_parses_values(self) -> None:
        assert Status.parse("verified") is Status.VERIFIED

    def test_rejects_unknown_values(self) -> None:
        with pytest.raises(InvalidValueError) as excinfo:
            Status.parse("done")
        assert excinfo.value.field == "status"
