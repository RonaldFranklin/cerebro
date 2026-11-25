import pytest

from cerebro.application.ports import SearchQuery
from cerebro.domain.access import AccessCeiling
from cerebro.domain.enums import Sensitivity
from cerebro.domain.errors import InvalidValueError


def test_text_must_be_a_string() -> None:
    with pytest.raises(InvalidValueError):
        SearchQuery(ceiling=AccessCeiling(Sensitivity.PUBLIC), text=3)  # type: ignore[arg-type]
