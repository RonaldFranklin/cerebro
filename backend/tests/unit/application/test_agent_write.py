from collections.abc import Callable
from dataclasses import replace

import pytest

from cerebro.application.agent_write import AgentWriteRequest, WriteRejected
from cerebro.application.errors import PermissionDenied
from cerebro.application.ports.notes import NoteChange
from cerebro.application.use_cases.agent_write import AgentWrite
from cerebro.domain.access import Scope
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.frontmatter import note_to_raw
from cerebro.domain.note import NoteId
from cerebro.domain.vocabulary import CORE
from tests.fakes.write_attempts import FakeWriteAttemptStore
from tests.unit.application.world import MESSAGE
from tests.unit.application.write_kit import SECRET, Kit, agent_ctx
from tests.unit.domain.builders import VALID_SUMMARY, VOCABULARY

CORE_WRITER = frozenset({Scope.READ, Scope.WRITE, Scope.CORE})
CORE_NOTE = {"type": "core", "priority": "P0", "hub": None, "title": "Global rule"}


def update_of(note_id: str, kit: Kit, **fields: object) -> AgentWriteRequest:
    note = kit.world.notes.get(NoteId(note_id))
    assert note is not None
    frontmatter = {k: v for k, v in note_to_raw(note).items() if k not in {"id", "created", "updated"}}
    return AgentWriteRequest(
        operation="update",
        target=note_id,
        base_hash=content_hash(note),
        frontmatter={**frontmatter, **fields},
        body=note.body + "Be concise.\n",
    )


def writer(kit: Kit, limit: int = 60) -> AgentWrite:
    w = kit.world
    return AgentWrite(w.notes, w.search, w.graph, FakeWriteAttemptStore(), kit.clock, kit.ids, VOCABULARY, limit)


def request(**fields: object) -> AgentWriteRequest:
    return AgentWriteRequest(
        operation="create",
        request_id="request-1",
        body="A synthetic procedure.",
        frontmatter={
            "title": "Direct write",
            "type": "guide",
            "area": "infrastructure",
            "priority": "P2",
            "sensitivity": "public",
            "hub": "infrastructure",
            "summary": VALID_SUMMARY,
            **fields,
        },
    )


def test_create_replay_conflict_update_and_stale_retry() -> None:
    kit = Kit()
    write = writer(kit)
    ctx = replace(agent_ctx(scopes=frozenset({Scope.WRITE, Scope.READ})), token_id="token-one")
    created = write.execute(request(), ctx)
    commits = kit.commits
    replay = write.execute(request(), ctx)
    assert (replay.note_id, replay.revision) == (created.note_id, created.revision)
    with pytest.raises(WriteRejected, match="request_conflict"):
        write.execute(replace(request(), body="Different"), ctx)
    note = kit.world.notes.get(created.note_id)
    assert note is not None
    update = replace(
        request(title="Renamed direct write"),
        operation="update",
        target=note.id.value,
        request_id=None,
        base_hash=content_hash(note),
        body="A corrected synthetic procedure.",
    )
    assert write.execute(update, ctx).note_id == created.note_id
    assert write.execute(request(), ctx).note_id == created.note_id
    assert kit.commits == commits + 1
    with pytest.raises(WriteRejected, match="stale_base"):
        write.execute(update, ctx)


@pytest.mark.parametrize("body", ["[[{}]]", "`[[{}]]`", "```\n[[{}]]\n```", "[[{}|alias]]"])
def test_hidden_and_missing_link_feedback(body: str) -> None:
    kit = Kit()
    write = writer(kit)
    ctx = replace(agent_ctx(scopes=frozenset({Scope.WRITE})), token_id="token-one")
    outputs = []
    for ref in ("vault-layout", "missing"):
        with pytest.raises(WriteRejected) as err:
            write.execute(replace(request(), body=body.format(ref)), ctx)
        outputs.append(err.value.findings)
    assert outputs[0] == outputs[1]
    assert outputs[0][0].code == "broken_link"


def test_invalid_calls_consume_quota_and_legacy_tokens_cannot_write() -> None:
    kit = Kit()
    write = writer(kit, 1)
    ctx = replace(agent_ctx(), token_id="token-one")
    with pytest.raises(PermissionDenied):
        write.execute(request(), ctx)
    ctx = replace(ctx, scopes=frozenset({Scope.WRITE}))
    commits = kit.commits
    with pytest.raises(WriteRejected) as err:
        write.execute(replace(request(), body=SECRET), ctx)
    assert err.value.findings[0].code == "secret_detected"
    assert SECRET not in repr(err.value.findings)
    with pytest.raises(WriteRejected, match="rate_limited"):
        write.execute(request(), ctx)
    assert kit.commits == commits
    other = replace(ctx, token_id="another-token")
    assert write.execute(request(), other).revision is not None


def test_hidden_and_missing_update_targets_match() -> None:
    kit = Kit()
    write = writer(kit)
    ctx = replace(agent_ctx(Sensitivity.PUBLIC, frozenset({Scope.WRITE})), token_id="token")
    for target in ("vault-layout", "unavailable"):
        with pytest.raises(WriteRejected, match="target_unavailable"):
            write.execute(
                replace(request(), operation="update", target=target, base_hash="a" * 64, request_id=None), ctx
            )


@pytest.mark.parametrize("body", ["a" * (48 * 1024 + 1), "\ud800"])
def test_non_storable_body_is_rejected_before_commit(body: str) -> None:
    kit = Kit()
    write = writer(kit)
    ctx = replace(agent_ctx(scopes=frozenset({Scope.WRITE})), token_id="token")
    before = kit.commits
    with pytest.raises(WriteRejected, match="invalid_field"):
        write.execute(replace(request(), body=body), ctx)
    assert kit.commits == before


@pytest.mark.parametrize(
    ("title", "stem"),
    [
        ("  RÉSUMÉ — São_Paulo 2!!", "resume-sao-paulo-2"),
        ("Ame\u0301lie + Cafe\u0301", "amelie-cafe"),
        ("a" * 59 + " - Manual", "a" * 59),
        ("b" * 75, "b" * 60),
        ("日本語", "note"),
    ],
)
def test_create_uses_a_readable_ascii_id(title: str, stem: str) -> None:
    kit = Kit()
    ctx = replace(agent_ctx(scopes=frozenset({Scope.WRITE})), token_id="token")
    assert writer(kit).execute(request(title=title), ctx).note_id == NoteId(f"{stem}-0001")


def test_create_can_retry_after_repeated_suffix_collisions(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = Kit()
    kit.world.add(id=NoteId("direct-write-k3f9"))
    write = writer(kit)
    ctx = replace(agent_ctx(scopes=frozenset({Scope.WRITE})), token_id="token")
    commits = kit.commits
    with monkeypatch.context() as patch:
        patch.setattr(kit.ids, "new_note_suffix", lambda: "k3f9")
        with pytest.raises(WriteRejected, match="id_unavailable"):
            write.execute(request(), ctx)
    assert kit.commits == commits
    assert write.execute(request(), ctx).note_id == NoteId("direct-write-0001")


def test_generated_collisions_are_retried_and_replay_rechecks_current_visibility() -> None:
    kit = Kit()
    kit.world.add(id=NoteId("direct-write-0001"), sensitivity=Sensitivity.RESTRICTED)
    write = writer(kit)
    ctx = replace(agent_ctx(scopes=frozenset({Scope.WRITE})), token_id="token")
    result = write.execute(request(), ctx)
    assert result.note_id.value == "direct-write-0002"
    note = kit.world.notes.get(result.note_id)
    assert note is not None

    kit.world.notes.apply(
        [NoteChange(note=replace(note, sensitivity=Sensitivity.RESTRICTED), expected_hash=content_hash(note))], MESSAGE
    )
    with pytest.raises(WriteRejected, match="target_unavailable"):
        write.execute(request(), ctx)


def test_narrower_listener_cannot_check_guesses_of_original_restricted_create() -> None:
    kit = Kit()
    write = writer(kit)
    private = replace(agent_ctx(Sensitivity.RESTRICTED, frozenset({Scope.WRITE})), token_id="token")
    original = request(sensitivity="restricted")
    receipt = write.execute(original, private)
    note = kit.world.notes.get(receipt.note_id)
    assert note is not None
    kit.world.notes.apply(
        [NoteChange(note=replace(note, sensitivity=Sensitivity.PUBLIC), expected_hash=content_hash(note))], MESSAGE
    )
    public = replace(agent_ctx(Sensitivity.PUBLIC, frozenset({Scope.WRITE})), token_id="token")
    for guess in (original, replace(original, body="Another restricted procedure.")):
        with pytest.raises(WriteRejected, match="outside_ceiling"):
            write.execute(guess, public)


def test_update_cannot_move_between_core_hubs_and_regular_notes() -> None:
    kit = Kit()
    write = writer(kit)
    ctx = replace(agent_ctx(scopes=CORE_WRITER), token_id="token")
    receipt = write.execute(request(), ctx)
    base = kit.world.notes.get(receipt.note_id)
    assert base is not None
    moved = replace(
        request(type="core", priority="P0", hub=None),
        operation="update",
        request_id=None,
        target=base.id.value,
        base_hash=content_hash(base),
    )
    with pytest.raises(WriteRejected, match="invalid_field"):
        write.execute(moved, ctx)
    assert kit.world.notes.head() == receipt.revision


def test_replay_cannot_confirm_a_former_reference_after_its_reclassification() -> None:
    kit = Kit()
    write = writer(kit)
    ctx = replace(agent_ctx(Sensitivity.PUBLIC, frozenset({Scope.WRITE})), token_id="token")
    ref = kit.world.add(id=NoteId("temporarily-public"))
    original = replace(request(), body="See [[temporarily-public]].")
    receipt = write.execute(original, ctx)
    note = kit.world.notes.get(receipt.note_id)
    assert note is not None
    kit.world.notes.apply(
        [NoteChange(note=replace(note, body="See the owner guide."), expected_hash=content_hash(note))], MESSAGE
    )
    kit.world.notes.apply(
        [NoteChange(note=replace(ref, sensitivity=Sensitivity.RESTRICTED), expected_hash=content_hash(ref))], MESSAGE
    )
    for guess in (original, replace(original, body="See [[never-existed]].")):
        with pytest.raises(WriteRejected, match="broken_link"):
            write.execute(guess, ctx)


@pytest.mark.parametrize(
    "make",
    [
        lambda kit: request(**CORE_NOTE),
        lambda kit: request(priority="P0"),
        lambda kit: update_of("core-rules", kit),
        lambda kit: update_of("core-rules", kit, status="archived"),
        lambda kit: update_of("core-rules", kit, status="superseded", superseded_by="infrastructure"),
        lambda kit: update_of("core-rules", kit, type="guide", priority="P2", hub="infrastructure"),
        lambda kit: update_of("server-ssh-access", kit, priority="P0"),
    ],
    ids=["create-core", "create-p0", "edit-core", "archive-core", "supersede-core", "demote-core", "promote-p0"],
)
def test_core_and_p0_notes_need_the_core_scope(make: Callable[[Kit], AgentWriteRequest]) -> None:
    kit = Kit()
    write = writer(kit)
    head = kit.world.notes.head()
    ctx = replace(agent_ctx(scopes=frozenset({Scope.READ, Scope.WRITE})), token_id="token")
    with pytest.raises(WriteRejected) as err:
        write.execute(make(kit), ctx)
    assert [f.code for f in err.value.findings] == ["core_scope_required"]
    assert kit.world.notes.head() == head


def test_core_scope_allows_core_writes_and_hubs_stay_open() -> None:
    kit = Kit()
    write = writer(kit)
    core = replace(agent_ctx(scopes=CORE_WRITER), token_id="core-token")
    assert write.execute(request(**CORE_NOTE), core).revision
    assert write.execute(update_of("core-rules", kit), core).revision
    plain = replace(agent_ctx(scopes=frozenset({Scope.WRITE})), token_id="plain-token")
    hub = request(type="hub", priority="P1", hub=None, title="Networking")
    assert write.execute(hub, plain).revision
    assert write.execute(update_of("infrastructure", kit), plain).revision


def test_create_replay_is_refused_once_the_stored_note_became_core() -> None:
    kit = Kit()
    write = writer(kit)
    ctx = replace(agent_ctx(scopes=frozenset({Scope.WRITE})), token_id="token")
    receipt = write.execute(request(), ctx)
    note = kit.world.notes.get(receipt.note_id)
    assert note is not None
    promoted = replace(note, type=CORE, priority=Priority.P0, hub=None)
    kit.world.notes.apply([NoteChange(note=promoted, expected_hash=content_hash(note))], MESSAGE)
    with pytest.raises(WriteRejected, match="core_scope_required"):
        write.execute(request(), ctx)
