import pytest

from cerebro.domain.errors import DomainError, InvalidValueError
from cerebro.domain.note import NoteId


@pytest.mark.parametrize("value", ["a", "server-ssh-access", "p0-rules", "x1-2-3", "a" * 80])
def test_accepts_kebab_case_ids_up_to_80_chars(value: str) -> None:
    assert NoteId(value).value == value
    assert str(NoteId(value)) == value


@pytest.mark.parametrize(
    "value",
    ["", "Server", "server_ssh", "-server", "server-", "server--ssh", "server ssh", "sérver", "a" * 81, "a/b"],
)
def test_rejects_invalid_ids(value: str) -> None:
    with pytest.raises(InvalidValueError) as excinfo:
        NoteId(value)
    assert excinfo.value.field == "id"
    assert isinstance(excinfo.value, DomainError)
    assert isinstance(excinfo.value, ValueError)


def test_is_a_hashable_sortable_value() -> None:
    assert NoteId("b") == NoteId("b")
    assert len({NoteId("b"), NoteId("b"), NoteId("a")}) == 2
    assert sorted([NoteId("b"), NoteId("a")]) == [NoteId("a"), NoteId("b")]


def test_is_immutable() -> None:
    note_id = NoteId("a")
    with pytest.raises(AttributeError):
        note_id.value = "b"  # type: ignore[misc]


@pytest.mark.parametrize("value", [2024, None, b"abc"])
def test_rejects_non_string_values_with_a_domain_error(value: object) -> None:
    with pytest.raises(InvalidValueError) as excinfo:
        NoteId(value)  # type: ignore[arg-type]
    assert excinfo.value.field == "id"
