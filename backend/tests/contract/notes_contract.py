"""Contract for ``NoteRepository``. Phase 2 adapters subclass it and override the ``repo`` fixture."""

from dataclasses import replace
from datetime import date

import pytest

from cerebro.application.ports import NoteChange, NoteRepository
from cerebro.application.ports.notes import CreateReceipt
from cerebro.domain.commit_message import AgentWriteCommit, CommitMessage, create_request_key
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Sensitivity
from cerebro.domain.errors import ContentConflict, InvalidValueError
from cerebro.domain.note import NoteId
from cerebro.domain.note_ref import NoteRef
from tests.unit.domain.builders import make_note

MESSAGE = CommitMessage(
    subject="Add note: server-ssh-access",
    proposal_id="proposal-0001",
    proposed_by="claude-code",
    curated_by="curator/null",
    approved_by="owner",
)


class NoteRepositoryContract:
    @pytest.fixture
    def repo(self) -> NoteRepository:
        raise NotImplementedError

    def test_create_receipt_survives_later_commits(self, repo: NoteRepository) -> None:
        note = make_note()
        key = create_request_key("token-one", "draft-one")
        fingerprint = "7" * 64
        message = AgentWriteCommit(note_id=note.id, written_by="test-agent", request_key=key, fingerprint=fingerprint)
        assert repo.find_create(key) is None
        revision = repo.apply([NoteChange(note=note)], message)
        newer = replace(note, body="New body.")
        repo.apply(
            [NoteChange(note=newer, expected_hash=content_hash(note))],
            AgentWriteCommit(note_id=note.id, written_by="other-agent"),
        )
        assert repo.find_create(key) == CreateReceipt(note_id=note.id, revision=revision, fingerprint=fingerprint)
        assert repo.find_create(create_request_key("other-token", "draft-one")) is None
        assert repo.head() != revision

    def test_agent_commit_must_match_change(self, repo: NoteRepository) -> None:
        note = make_note()
        with pytest.raises(InvalidValueError):
            repo.apply([NoteChange(note=note)], AgentWriteCommit(note_id=note.id, written_by="test-agent"))
        assert repo.head() is None

    def test_get_unknown_returns_none(self, repo: NoteRepository) -> None:
        assert repo.get(NoteId("nothing-here")) is None
        assert repo.list_notes() == ()
        assert repo.list_refs() == ()

    def test_create_then_get_roundtrips(self, repo: NoteRepository) -> None:
        note = make_note()
        revision = repo.apply([NoteChange(note=note)], MESSAGE)
        assert revision
        assert repo.get(note.id) == note

    def test_each_apply_returns_a_new_revision(self, repo: NoteRepository) -> None:
        first = repo.apply([NoteChange(note=make_note(id=NoteId("note-a")))], MESSAGE)
        second = repo.apply([NoteChange(note=make_note(id=NoteId("note-b")))], MESSAGE)
        assert first != second

    def test_head_is_none_for_an_empty_repository(self, repo: NoteRepository) -> None:
        assert repo.head() is None

    def test_head_is_the_revision_apply_returned_and_changes_with_each_commit(self, repo: NoteRepository) -> None:
        first = repo.apply([NoteChange(note=make_note(id=NoteId("note-a")))], MESSAGE)
        assert repo.head() == first
        second = repo.apply([NoteChange(note=make_note(id=NoteId("note-b")))], MESSAGE)
        assert repo.head() == second != first

    def test_head_is_stable_across_reads_and_failed_applies(self, repo: NoteRepository) -> None:
        revision = repo.apply([NoteChange(note=make_note())], MESSAGE)
        repo.list_notes()
        repo.list_refs()
        repo.get(make_note().id)
        assert repo.head() == revision
        with pytest.raises(ContentConflict):
            repo.apply([NoteChange(note=make_note())], MESSAGE)  # already exists
        assert repo.head() == revision

    def test_listings_are_ordered_by_id_and_refs_match_notes(self, repo: NoteRepository) -> None:
        for name in ("zeta-note", "alpha-note", "mid-note"):
            repo.apply([NoteChange(note=make_note(id=NoteId(name)))], MESSAGE)
        ids = [n.id.value for n in repo.list_notes()]
        assert ids == ["alpha-note", "mid-note", "zeta-note"]
        assert [r.id.value for r in repo.list_refs()] == ids

    def test_refs_equal_the_refs_built_from_the_stored_notes(self, repo: NoteRepository) -> None:
        body = "[[a-note]] `[[b-note]]` [[c-note|alias]]"
        repo.apply([NoteChange(note=make_note(id=NoteId("note-x"), body=body))], MESSAGE)
        (ref,) = repo.list_refs()
        (note,) = repo.list_notes()
        assert ref == NoteRef.from_note(note)
        assert [t.value for t in ref.loose_body_targets] == ["a-note", "b-note", "c-note"]

    def test_refs_carry_no_body_but_the_extracted_links(self, repo: NoteRepository) -> None:
        repo.apply([NoteChange(note=make_note(body="See [[other-note#Part]]."))], MESSAGE)
        (ref,) = repo.list_refs()
        assert [(w.target.value, w.section) for w in ref.body_links] == [("other-note", "Part")]
        assert not hasattr(ref, "body")

    def test_update_with_matching_hash_succeeds(self, repo: NoteRepository) -> None:
        note = make_note()
        repo.apply([NoteChange(note=note)], MESSAGE)
        newer = replace(note, body="Changed.\n")
        repo.apply([NoteChange(note=newer, expected_hash=content_hash(note))], MESSAGE)
        assert repo.get(note.id) == newer

    def test_update_with_stale_hash_conflicts_and_changes_nothing(self, repo: NoteRepository) -> None:
        note = make_note()
        repo.apply([NoteChange(note=note)], MESSAGE)
        stale = content_hash(note)
        current = replace(note, body="Edited by someone else.\n")
        repo.apply([NoteChange(note=current, expected_hash=stale)], MESSAGE)
        with pytest.raises(ContentConflict) as info:
            repo.apply([NoteChange(note=replace(note, body="Mine.\n"), expected_hash=stale)], MESSAGE)
        assert info.value.expected == stale
        assert info.value.actual == content_hash(current)
        assert repo.get(note.id) == current

    def test_create_over_existing_note_conflicts(self, repo: NoteRepository) -> None:
        note = make_note()
        repo.apply([NoteChange(note=note)], MESSAGE)
        with pytest.raises(ContentConflict):
            repo.apply([NoteChange(note=replace(note, body="Other.\n"))], MESSAGE)
        assert repo.get(note.id) == note

    def test_update_of_missing_note_conflicts(self, repo: NoteRepository) -> None:
        with pytest.raises(ContentConflict):
            repo.apply([NoteChange(note=make_note(), expected_hash=content_hash(make_note()))], MESSAGE)
        assert repo.get(make_note().id) is None

    def test_apply_is_atomic(self, repo: NoteRepository) -> None:
        existing = make_note(id=NoteId("existing-note"))
        repo.apply([NoteChange(note=existing)], MESSAGE)
        fresh = make_note(id=NoteId("fresh-note"))
        edited = replace(existing, body="Edited.\n")
        with pytest.raises(ContentConflict):
            repo.apply(
                [
                    NoteChange(note=fresh),
                    NoteChange(
                        note=edited, expected_hash=content_hash(make_note(id=NoteId("existing-note"), body="x"))
                    ),
                ],
                MESSAGE,
            )
        assert repo.get(fresh.id) is None
        assert repo.get(existing.id) == existing

    def test_multi_note_apply_writes_every_note(self, repo: NoteRepository) -> None:
        a, b = make_note(id=NoteId("note-a")), make_note(id=NoteId("note-b"))
        repo.apply([NoteChange(note=a), NoteChange(note=b)], MESSAGE)
        assert repo.get(a.id) == a
        assert repo.get(b.id) == b

    def test_empty_change_list_is_rejected(self, repo: NoteRepository) -> None:
        with pytest.raises(InvalidValueError):
            repo.apply([], MESSAGE)

    def test_same_note_twice_is_rejected_without_writing(self, repo: NoteRepository) -> None:
        note = make_note()
        with pytest.raises(InvalidValueError):
            repo.apply([NoteChange(note=note), NoteChange(note=note)], MESSAGE)
        assert repo.get(note.id) is None

    def test_note_change_rejects_wrong_types(self) -> None:
        with pytest.raises(InvalidValueError):
            NoteChange(note="not a note")  # type: ignore[arg-type]
        with pytest.raises(InvalidValueError):
            NoteChange(note=make_note(), expected_hash=3)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "text",
        [
            "no",
            "yes",
            "on",
            "off",
            "true",
            "null",
            "~",
            "y",
            "n",
            "2026",
            "0x1f",
            "1e3",
            "1_000",
            "1:30",
            ".inf",
            "2026-11-24",
            "2026-11-24 10:00:00",
            "-1",
            "+.5",
            "=",
            "<<",
            "  padded  ",
            "a: b",
            "- item",
            "# not a comment",
            "line one\nline two",
            "tab\tinside",
            "quote ' and \" both",
            "ünïcödé ✓ 日本語",
            "---",
            "...",
            "&anchor",
            "*alias",
            "!tag",
            "{flow}",
            "[list]",
            "@at",
            "`tick`",
            "%percent",
            "trailing colon:",
            "emoji \U0001f600",
        ],
    )
    def test_tricky_text_roundtrips_exactly(self, repo: NoteRepository, text: str) -> None:
        note = make_note(title=text, summary=text, lang=text, body=text)
        repo.apply([NoteChange(note=note)], MESSAGE)
        stored = repo.get(note.id)
        assert stored == note
        assert content_hash(stored) == content_hash(note)

    @pytest.mark.parametrize(
        "body",
        [
            "",
            "no trailing newline",
            "\n",
            "\n\n\n",
            "crlf\r\nlines\r\n",
            "lone\rcr",
            "---\nnot frontmatter\n---\n",
            "\ufeffbom",
        ],
    )
    def test_bodies_roundtrip_byte_for_byte(self, repo: NoteRepository, body: str) -> None:
        note = make_note(body=body)
        repo.apply([NoteChange(note=note)], MESSAGE)
        assert repo.get(note.id) == note
        assert repo.list_notes() == (note,)

    def test_optional_fields_roundtrip(self, repo: NoteRepository) -> None:
        note = make_note(review_by=date(2027, 5, 24), lang="pt-BR", related=())
        repo.apply([NoteChange(note=note)], MESSAGE)
        assert repo.get(note.id) == note

    def test_sensitivity_changes_roundtrip_in_both_directions(self, repo: NoteRepository) -> None:
        note = make_note(sensitivity=Sensitivity.PUBLIC)
        repo.apply([NoteChange(note=note)], MESSAGE)
        restricted = replace(note, sensitivity=Sensitivity.RESTRICTED)
        repo.apply([NoteChange(note=restricted, expected_hash=content_hash(note))], MESSAGE)
        assert repo.get(note.id) == restricted
        internal = replace(note, sensitivity=Sensitivity.INTERNAL)
        repo.apply([NoteChange(note=internal, expected_hash=content_hash(restricted))], MESSAGE)
        assert repo.get(note.id) == internal
