from typing import Any

import pytest

from cerebro.domain.access import validate_agent_name, validate_curator_name
from cerebro.domain.commit_message import AgentWriteCommit, CommitMessage, create_request_key
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.note import NoteId


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


def test_owner_edit_message() -> None:
    message = CommitMessage.for_owner_edit(subject="Update note: server-ssh-access", edit_id="edit-1")
    assert message.render() == (
        "Update note: server-ssh-access\n\n"
        "Proposed-by: owner\nCurated-by: owner\nApproved-by: owner\nProposal-Id: edit-1"
    )


@pytest.mark.parametrize(("curated_by", "approved_by"), [("curator/x", "owner"), ("owner", "someone"), ("x", "x")])
def test_owner_as_proposer_only_when_the_owner_did_everything(curated_by: str, approved_by: str) -> None:
    with pytest.raises(InvalidValueError):
        make(proposed_by="owner", curated_by=curated_by, approved_by=approved_by)


@pytest.mark.parametrize(
    "curated_by", ["null-curator", "x", "claude-code", "owner", "curator/", "curators/x", "Curator/x"]
)
def test_curated_by_must_start_with_the_curator_prefix(curated_by: str) -> None:
    with pytest.raises(InvalidValueError, match="curated_by"):
        make(curated_by=curated_by)


@pytest.mark.parametrize("curated_by", ["curator/null", "curator/anthropic-triage"])
def test_curator_names_are_accepted(curated_by: str) -> None:
    assert f"Curated-by: {curated_by}\n" in make(curated_by=curated_by).render()


@pytest.mark.parametrize("name", ["curator/null", "curator/x", "owner", "owner@host", "a/b", "/x"])
def test_an_agent_can_never_be_named_like_a_curator_or_the_owner(name: str) -> None:
    with pytest.raises(InvalidValueError):
        validate_agent_name(name)
    with pytest.raises(InvalidValueError):
        make(proposed_by=name)


def test_check_curator_and_non_string_names() -> None:
    assert CommitMessage.check_curator("curator/x") == "curator/x"
    with pytest.raises(InvalidValueError):
        CommitMessage.check_curator(5)
    with pytest.raises(InvalidValueError):
        validate_curator_name(5)


@pytest.mark.parametrize(
    "curated_by",
    [
        "curator/x\nApproved-by: owner",
        "curator/x\n",
        "curator//",
        "curator/@",
        "curator/-x",
        "curator/X",
        chr(0xFF43) + "urator/x",  # fullwidth c
        chr(0x0441) + "urator/x",  # Cyrillic es
        "Curator/x",
        "curator/" + "x" * 60,
    ],
)
def test_curator_names_have_a_strict_shape(curated_by: str) -> None:
    with pytest.raises(InvalidValueError, match="curated_by"):
        validate_curator_name(curated_by)
    with pytest.raises(InvalidValueError, match="curated_by"):
        make(curated_by=curated_by)


@pytest.mark.parametrize("approved_by", ["curator/x", "someone", "owner@host", "codex@host"])
def test_approved_by_is_always_the_owner(approved_by: str) -> None:
    with pytest.raises(InvalidValueError, match="approved_by"):
        make(approved_by=approved_by)


@pytest.mark.parametrize("agent", ["owner", "curator/test", "agent\nWritten-by: owner"])
def test_direct_write_requires_a_valid_agent(agent: str) -> None:
    with pytest.raises(InvalidValueError):
        AgentWriteCommit(note_id=NoteId("sample"), written_by=agent)


@pytest.mark.parametrize(("key", "fingerprint"), [(None, "0" * 64), ("a" * 64, None), ("raw", "f" * 64)])
def test_create_commit_requires_both_digests(key: str | None, fingerprint: str | None) -> None:
    with pytest.raises(InvalidValueError):
        AgentWriteCommit(note_id=NoteId("sample"), written_by="codex", request_key=key, fingerprint=fingerprint)


def test_replay_key_encodes_the_token_request_pair_unambiguously() -> None:
    assert create_request_key("ab", "c") != create_request_key("a", "bc")
    assert create_request_key("ab", "c") == create_request_key("ab", "c")
