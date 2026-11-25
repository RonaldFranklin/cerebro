import pytest

from cerebro.domain.decision import Decision, DecisionKind
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.note import NoteId

N = NoteId("some-note")


def test_valid_variants() -> None:
    assert Decision.reject("duplicate").kind is DecisionKind.REJECT
    assert Decision.merge_into(N).target == N
    assert Decision.create(N, "new topic").reason == "new topic"
    assert Decision.needs_owner("sensitive").target is None
    assert Decision.needs_owner("sensitive", N).target == N


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_reject_and_needs_owner_require_reason(reason: str | None) -> None:
    with pytest.raises(InvalidValueError, match="reason"):
        Decision(kind=DecisionKind.REJECT, reason=reason)
    with pytest.raises(InvalidValueError, match="reason"):
        Decision(kind=DecisionKind.NEEDS_OWNER, reason=reason)


@pytest.mark.parametrize("kind", [DecisionKind.MERGE_INTO, DecisionKind.CREATE])
def test_merge_and_create_require_target(kind: DecisionKind) -> None:
    with pytest.raises(InvalidValueError, match="target"):
        Decision(kind=kind)


def test_reject_forbids_target() -> None:
    with pytest.raises(InvalidValueError, match="target"):
        Decision(kind=DecisionKind.REJECT, reason="no", target=N)


def test_type_and_length_checks() -> None:
    with pytest.raises(InvalidValueError):
        Decision(kind="reject", reason="x")  # type: ignore[arg-type]
    with pytest.raises(InvalidValueError):
        Decision(kind=DecisionKind.CREATE, target="x")  # type: ignore[arg-type]
    with pytest.raises(InvalidValueError):
        Decision(kind=DecisionKind.CREATE, target=N, reason=5)  # type: ignore[arg-type]
    with pytest.raises(InvalidValueError, match="longer"):
        Decision.reject("x" * 1001)
