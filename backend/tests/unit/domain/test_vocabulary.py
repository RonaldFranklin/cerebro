import pytest

from cerebro.domain.errors import InvalidValueError
from cerebro.domain.vocabulary import DEFAULT_NOTE_TYPES, Area, NoteType, Vocabulary


def test_area_and_type_are_kebab_case_values() -> None:
    assert Area("infrastructure").value == "infrastructure"
    assert str(Area("infrastructure")) == "infrastructure"
    assert str(NoteType("runbook")) == "runbook"


@pytest.mark.parametrize(("cls", "field"), [(Area, "area"), (NoteType, "type")])
@pytest.mark.parametrize("value", ["", "Infra", "infra_structure", "-infra"])
def test_rejects_non_kebab_case(cls: type[Area] | type[NoteType], field: str, value: str) -> None:
    with pytest.raises(InvalidValueError) as excinfo:
        cls(value)
    assert excinfo.value.field == field


def test_default_note_types_match_the_domain_model() -> None:
    assert {t.value for t in DEFAULT_NOTE_TYPES} == {
        "core",
        "hub",
        "concept",
        "guide",
        "runbook",
        "reference",
        "config",
        "decision",
        "project",
    }


def test_vocabulary_answers_membership() -> None:
    vocabulary = Vocabulary(areas=frozenset({Area("infrastructure")}), types=DEFAULT_NOTE_TYPES)

    assert vocabulary.has_area(Area("infrastructure"))
    assert not vocabulary.has_area(Area("cooking"))
    assert vocabulary.has_type(NoteType("runbook"))
    assert not vocabulary.has_type(NoteType("recipe"))


@pytest.mark.parametrize("missing", ["core", "hub"])
def test_vocabulary_must_contain_the_types_the_domain_relies_on(missing: str) -> None:
    types = frozenset(t for t in DEFAULT_NOTE_TYPES if t.value != missing)
    with pytest.raises(InvalidValueError) as excinfo:
        Vocabulary(areas=frozenset(), types=types)
    assert excinfo.value.field == "types"
