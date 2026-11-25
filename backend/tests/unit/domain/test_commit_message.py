from typing import Any

import pytest

from cerebro.domain.commit_message import CommitMessage
from cerebro.domain.errors import InvalidValueError


def make(**overrides: Any) -> CommitMessage:
    fields: dict[str, Any] = {
        "subject": "Add note: server-ssh-access",
        "proposal_id": "0193-abc",
        "proposed_by": "codex@workstation",
        "curated_by": "curator/some-model",
        "approved_by": "owner",
    }
    fields.update(overrides)
    return CommitMessage(**fields)


def test_render() -> None:
    assert make().render() == (
        "Add note: server-ssh-access\n\n"
        "Proposed-by: codex@workstation\n"
        "Curated-by: curator/some-model\n"
        "Approved-by: owner\n"
        "Proposal-Id: 0193-abc"
    )


@pytest.mark.parametrize(
    "subject",
    ["", "  ", "a\nProposed-by: owner", "a\rb", "a\x00b", "a" + chr(0x2028) + "b", " lead", "trail ", "x" * 101],
)
def test_bad_subject(subject: str) -> None:
    with pytest.raises(InvalidValueError, match="subject"):
        make(subject=subject)


@pytest.mark.parametrize("field", ["curated_by", "approved_by"])
@pytest.mark.parametrize(
    "value",
    ["", "owner\nApproved-by: owner", "a b", "a\tb", "x\r", "-lead", "a:b", "é", "x" * 101, "a" + chr(0x2028) + "b", 5],
)
def test_trailer_injection_rejected(field: str, value: Any) -> None:
    with pytest.raises(InvalidValueError, match=field):
        make(**{field: value})


@pytest.mark.parametrize("value", ["owner", "codex@host\nApproved-by: owner", "OWNER@x", "a b", "", "bad name"])
def test_proposed_by_must_be_an_agent_name(value: str) -> None:
    with pytest.raises(InvalidValueError):
        make(proposed_by=value)


@pytest.mark.parametrize("value", ["", "a\nb", "a b", "x" * 65, 3])
def test_bad_proposal_id(value: Any) -> None:
    with pytest.raises(InvalidValueError, match="proposal_id"):
        make(proposal_id=value)


def test_check_attribution() -> None:
    assert CommitMessage.check_attribution("owner", "approved_by") == "owner"
