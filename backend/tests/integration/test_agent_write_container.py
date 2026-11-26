"""Exercise the direct writer through bootstrap with actual Git, SQLite and authentication."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from cerebro.application.agent_write import AgentWriteRequest, WriteRejected
from cerebro.application.dto import AuthenticateTokenRequest, IssueAgentTokenRequest
from cerebro.application.index_state import index_is_stale
from cerebro.bootstrap.container import Container, build_container
from cerebro.domain.access import AccessCeiling, AccessContext, Scope
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Sensitivity
from tests.integration.content_kit import BROKEN_ID, HUB_ID, make_sample_repo
from tests.unit.domain.builders import VALID_SUMMARY

OWNER = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))


def writer_context(container: Container) -> AccessContext:
    issued = container.use_cases.issue_agent_token.execute(
        IssueAgentTokenRequest(agent="codex", scopes=frozenset({Scope.READ, Scope.WRITE}), ceiling=OWNER.ceiling),
        OWNER,
    )
    return container.use_cases.authenticate_token.execute(
        AuthenticateTokenRequest(secret=issued.secret, listener=AccessCeiling(Sensitivity.INTERNAL))
    )


def create() -> AgentWriteRequest:
    return AgentWriteRequest(
        operation="create",
        request_id="fixture-1",
        frontmatter={
            "title": "Restart procedure",
            "type": "guide",
            "area": "infrastructure",
            "priority": "P2",
            "sensitivity": "internal",
            "hub": HUB_ID.value,
            "summary": VALID_SUMMARY,
        },
        body="Read the status before restarting.",
    )


def test_committed_create_survives_failed_index_update_and_lost_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = make_sample_repo(tmp_path)
    service = build_container(sample.settings)
    service.start()
    ctx = writer_context(service)

    def interrupted(*args: object) -> None:
        raise OSError("synthetic index interruption")

    with monkeypatch.context() as patch:
        patch.setattr(service.search, "upsert", interrupted)
        receipt = service.use_cases.agent_write.execute(create(), ctx)
    assert service.notes.head() == receipt.revision
    assert index_is_stale(service.notes, service.search, service.graph)

    # The next writer rebuilds under the lock before it commits, so both indexes end fresh.
    later = service.use_cases.agent_write.execute(replace(create(), request_id="fixture-2"), ctx)
    assert later.revision != receipt.revision
    assert not index_is_stale(service.notes, service.search, service.graph)
    assert service.use_cases.agent_write.execute(create(), ctx).revision == receipt.revision
    assert service.notes.head() == later.revision


def test_two_service_instances_only_commit_one_update_from_the_same_base(tmp_path: Path) -> None:
    sample = make_sample_repo(tmp_path)
    first = build_container(sample.settings)
    first.start()
    ctx = writer_context(first)
    receipt = first.use_cases.agent_write.execute(create(), ctx)
    note = first.notes.get(receipt.note_id)
    assert note is not None
    update = replace(create(), operation="update", target=note.id.value, base_hash=content_hash(note), request_id=None)
    second = build_container(sample.settings)

    def write(service: Container, body: str) -> str:
        try:
            return service.use_cases.agent_write.execute(replace(update, body=body), ctx).revision
        except WriteRejected as error:
            return error.findings[0].code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, (first, second), ("First candidate.", "Second candidate.")))
    assert "stale_base" in outcomes
    assert first.notes.head() in outcomes
    assert not index_is_stale(first.notes, first.search, first.graph)


def test_redacted_note_is_protected_even_with_the_full_storage_hash(tmp_path: Path) -> None:
    service = build_container(make_sample_repo(tmp_path).settings)
    service.start()
    ctx = writer_context(service)
    broken = service.notes.get(BROKEN_ID)
    assert broken is not None
    before = service.notes.head()
    unsafe = replace(
        create(), operation="update", target=BROKEN_ID.value, base_hash=content_hash(broken), request_id=None
    )
    with pytest.raises(WriteRejected):
        service.use_cases.agent_write.execute(unsafe, ctx)
    assert service.notes.head() == before


@pytest.mark.parametrize("sensitivity", ["internal", "restricted"])
def test_unchanged_update_has_one_safe_outcome_for_plaintext_and_encrypted_notes(
    tmp_path: Path, sensitivity: str
) -> None:
    container = build_container(make_sample_repo(tmp_path).settings)
    container.start()
    issued = container.use_cases.issue_agent_token.execute(
        IssueAgentTokenRequest(agent="codex", scopes=frozenset({Scope.WRITE}), ceiling=OWNER.ceiling), OWNER
    )
    ctx = container.use_cases.authenticate_token.execute(
        AuthenticateTokenRequest(secret=issued.secret, listener=OWNER.ceiling)
    )
    original = create()
    assert isinstance(original.frontmatter, dict)
    original = replace(original, frontmatter={**original.frontmatter, "sensitivity": sensitivity})
    receipt = container.use_cases.agent_write.execute(original, ctx)
    note = container.notes.get(receipt.note_id)
    assert note is not None
    unchanged = replace(
        original, operation="update", target=note.id.value, request_id=None, base_hash=content_hash(note)
    )
    with pytest.raises(WriteRejected, match="unchanged"):
        container.use_cases.agent_write.execute(unchanged, ctx)
    assert container.notes.head() == receipt.revision
