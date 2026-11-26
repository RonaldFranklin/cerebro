from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

import pytest

from cerebro.application.agent_write import AgentWriteRequest, WriteRejected
from cerebro.application.dto import GetVocabularyRequest
from cerebro.application.errors import PermissionDenied
from cerebro.application.index_state import index_is_stale
from cerebro.application.ports.notes import NoteChange
from cerebro.application.ports.search import SearchQuery
from cerebro.application.use_cases.agent_write import AgentWrite
from cerebro.application.use_cases.get_vocabulary import GetVocabulary
from cerebro.domain.access import AccessContext, Scope
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


def test_replay_compares_with_the_originally_committed_note_and_stays_per_token() -> None:
    kit = Kit()
    write = writer(kit)
    ctx = replace(agent_ctx(scopes=frozenset({Scope.WRITE, Scope.READ})), token_id="token-one")
    created = write.execute(request(), ctx)
    kit.clock.advance(timedelta(days=3))
    replay = write.execute(request(), ctx)
    assert (replay.note_id, replay.revision) == (created.note_id, created.revision)
    with pytest.raises(WriteRejected, match="request_conflict"):
        write.execute(request(priority="P3"), ctx)
    other = write.execute(request(), replace(ctx, token_id="token-two"))
    assert other.note_id != created.note_id


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
    assert outputs[0][0].field == "body"


@pytest.mark.parametrize(
    ("changes", "body", "field", "position"),
    [
        ({"hub": "{}"}, "Text.", "hub", "hub"),
        ({"related": ["server-ssh-access", "{}"]}, "Text.", "related", "related entry #2"),
        ({"status": "superseded", "superseded_by": "{}"}, "Text.", "superseded_by", "superseded_by"),
        ({}, "Intro.\n\n```toml\n[[{}]]\n```", "body", "line 4 of body"),
        ({}, "First.\u2028Still first [[\u2028{}]]", "body", "line 1 of body"),
    ],
)
def test_unavailable_references_name_field_and_position(
    changes: dict[str, object], body: str, field: str, position: str
) -> None:
    kit = Kit()
    ctx = replace(agent_ctx(scopes=frozenset({Scope.WRITE})), token_id="token-one")
    outputs = []
    for ref in ("vault-layout", "never-written"):
        filled = {
            key: [item.format(ref) for item in value] if isinstance(value, list) else str(value).format(ref)
            for key, value in changes.items()
        }
        with pytest.raises(WriteRejected) as err:
            writer(kit).execute(replace(request(**filled), body=body.format(ref)), ctx)
        outputs.append(err.value.findings)
    assert outputs[0] == outputs[1]
    (finding,) = outputs[0]
    assert (finding.code, finding.field) == ("broken_link", field)
    assert position in finding.hint.lower()
    assert "[[note-id]]" in finding.hint
    assert "vault-layout" not in finding.hint


@pytest.mark.parametrize(
    ("changes", "code", "field", "expected"),
    [
        ({"review_by": "next June"}, "invalid_field", "review_by", "YYYY-MM-DD"),
        ({"priority": "urgent"}, "invalid_field", "priority", "P0, P1, P2, P3"),
        ({"type": "memo"}, "unknown_type", "type", "cerebro_write_guide"),
        ({"area": "cooking"}, "unknown_area", "area", "cerebro_write_guide"),
        ({"hub": None}, "hub_required", "hub", "type hub"),
    ],
)
def test_field_hints_say_what_is_accepted(changes: dict[str, object], code: str, field: str, expected: str) -> None:
    ctx = replace(agent_ctx(scopes=frozenset({Scope.WRITE})), token_id="token-one")
    with pytest.raises(WriteRejected) as err:
        writer(Kit()).execute(request(**changes), ctx)
    (finding,) = err.value.findings
    assert (finding.code, finding.field) == (code, field)
    assert expected in finding.hint
    assert str(changes[field]) not in finding.hint


def test_lowering_sensitivity_hint_says_to_keep_it() -> None:
    kit = Kit()
    ctx = replace(agent_ctx(scopes=frozenset({Scope.WRITE})), token_id="token-one")
    with pytest.raises(WriteRejected) as err:
        writer(kit).execute(update_of("server-ssh-access", kit, sensitivity="public"), ctx)
    finding = next(f for f in err.value.findings if f.field == "sensitivity" and f.code == "invalid_field")
    assert "cannot lower" in finding.hint
    assert "keep" in finding.hint


def test_vocabulary_is_served_only_to_writers() -> None:
    case = GetVocabulary(VOCABULARY)
    assert case.execute(GetVocabularyRequest(), agent_ctx(scopes=frozenset({Scope.WRITE}))) == VOCABULARY
    with pytest.raises(PermissionDenied):
        case.execute(GetVocabularyRequest(), agent_ctx())


def test_invalid_calls_consume_quota_and_read_tokens_cannot_write() -> None:
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


LONG_TITLES = (
    ("Configuring PostgreSQL 16 backups with pgBackRest on Ubuntu 24.04", "k7q2"),
    ("Quarterly review 2026 Q4 of Zigbee2MQTT and Mosquitto upgrade", "x9m3"),
)


@pytest.mark.parametrize(("title", "suffix"), LONG_TITLES)
def test_ids_from_long_titles_with_digits_are_not_secrets(
    title: str, suffix: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = Kit()
    write = writer(kit)
    ctx = replace(agent_ctx(scopes=frozenset({Scope.WRITE, Scope.READ})), token_id="token")
    monkeypatch.setattr(kit.ids, "new_note_suffix", lambda: suffix)
    first = write.execute(request(title=title), ctx).note_id
    assert first.value.endswith(f"-{suffix}")
    assert len(first.value) > 60
    monkeypatch.setattr(kit.ids, "new_note_suffix", lambda: "a0a0")
    linking = request(title="Backup overview", related=[first.value])
    body = f"See [[{first.value}]], or read `{first.value}` when {first.value} changes.\n"
    second = write.execute(replace(linking, request_id="request-2", body=body), ctx).note_id
    assert write.execute(update_of(first.value, kit), ctx).note_id == first
    assert write.execute(update_of(second.value, kit), ctx).note_id == second


def test_plain_ids_of_hidden_notes_are_scanned_like_unknown_tokens() -> None:
    kit = Kit()
    hidden = NoteId("configuring-postgresql-16-backups-with-pgbackrest-on-ubuntu-24-04-k7q2")
    kit.world.add(id=hidden, hub=NoteId("infrastructure"), sensitivity=Sensitivity.RESTRICTED)
    write = writer(kit)
    ctx = replace(agent_ctx(scopes=frozenset({Scope.WRITE})), token_id="token")
    outputs = []
    for ref in (hidden.value, hidden.value.replace("k7q2", "z5w8")):
        with pytest.raises(WriteRejected) as err:
            write.execute(replace(request(), body=f"Mentions {ref} in passing.\n"), ctx)
        outputs.append(err.value.findings)
    assert outputs[0] == outputs[1]
    assert [f.code for f in outputs[0]] == ["secret_detected"]


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


@pytest.mark.parametrize("change", [{"sensitivity": "restricted"}, {"status": "archived"}])
def test_a_failed_commit_leaves_the_indexes_untouched_and_fresh(change: dict[str, str]) -> None:
    kit = Kit()
    w = kit.world
    ctx = replace(agent_ctx(Sensitivity.RESTRICTED, frozenset({Scope.WRITE})), token_id="token-one")
    created = writer(kit).execute(request(), ctx)
    indexed = w.search.search(_query(ctx))
    w.notes.apply_error = OSError("synthetic write failure")
    with pytest.raises(OSError, match="synthetic"):
        writer(kit).execute(update_of(created.note_id.value, kit, **change), ctx)
    assert w.notes.head() == created.revision
    assert not index_is_stale(w.notes, w.search, w.graph)
    assert w.search.search(_query(ctx)) == indexed


@pytest.mark.parametrize("method", ["upsert", "set_revision"])
def test_a_failed_index_update_after_the_commit_is_flagged_not_raised(method: str) -> None:
    kit = Kit()
    w = kit.world
    ctx = replace(agent_ctx(Sensitivity.RESTRICTED, frozenset({Scope.WRITE})), token_id="token-one")

    def boom(*args: object) -> None:
        raise OSError("synthetic index failure")

    setattr(w.graph, method, boom)
    created = writer(kit).execute(request(), ctx)
    assert w.notes.head() == created.revision
    assert index_is_stale(w.notes, w.search, w.graph)
    # A replay reads the commit history, not the stale index.
    replay = writer(kit).execute(request(), ctx)
    assert (replay.note_id, replay.revision) == (created.note_id, created.revision)
    assert w.notes.head() == created.revision


def _query(ctx: AccessContext) -> SearchQuery:
    return SearchQuery(ceiling=ctx.ceiling, text="Direct write")
