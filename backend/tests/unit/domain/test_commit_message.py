from typing import Any

import pytest

from cerebro.domain.access import validate_agent_name
from cerebro.domain.commit_message import AgentWriteCommit, OwnerCommit, create_request_key
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.note import NoteId


def test_owner_edit_is_written_by_the_owner() -> None:
    assert OwnerCommit.for_update(NoteId("server-ssh-access")).render() == (
        "Update note: server-ssh-access\n\nWritten-by: owner"
    )


def test_owner_edit_requires_a_note_id() -> None:
    with pytest.raises(InvalidValueError, match="note_id"):
        OwnerCommit.for_update("server-ssh-access")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "subject",
    ["", "  ", "a\nWritten-by: x", "a\rb", "a\x00b", "a" + chr(0x2028) + "b", " lead", "trail ", "x" * 101, 5],
)
def test_bad_subject(subject: Any) -> None:
    with pytest.raises(InvalidValueError, match="subject"):
        OwnerCommit(subject=subject)


@pytest.mark.parametrize("name", ["owner", "owner@host", "curator/x", "a/b", "/x"])
def test_an_agent_can_never_be_named_like_the_owner(name: str) -> None:
    with pytest.raises(InvalidValueError):
        validate_agent_name(name)


@pytest.mark.parametrize("agent", ["owner", "curator/test", "agent\nWritten-by: owner"])
def test_direct_write_requires_a_valid_agent(agent: str) -> None:
    with pytest.raises(InvalidValueError):
        AgentWriteCommit(note_id=NoteId("sample"), written_by=agent)


@pytest.mark.parametrize("key", ["raw", "A" * 64, "a" * 63, "a" * 64 + "\nWritten-by: owner"])
def test_create_commit_requires_a_digest_request_key(key: str) -> None:
    with pytest.raises(InvalidValueError):
        AgentWriteCommit(note_id=NoteId("sample"), written_by="codex", request_key=key)


def test_replay_key_encodes_the_token_request_pair_unambiguously() -> None:
    assert create_request_key("ab", "c") != create_request_key("a", "bc")
    assert create_request_key("ab", "c") == create_request_key("ab", "c")
