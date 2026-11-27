from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from cerebro.application.dto import (
    ApplyWarningCode,
    OwnerEditNoteRequest,
)
from cerebro.application.errors import (
    NoteNotFound,
    PermissionDenied,
)
from cerebro.application.index_state import index_is_stale
from cerebro.domain.content_hash import content_hash
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import ContentConflict, InvalidValueError
from cerebro.domain.note import Note
from cerebro.domain.report import ViolationCode
from tests.unit.application.world import NID
from tests.unit.application.write_kit import (
    SECRET,
    SSH,
    Kit,
    agent_ctx,
    note_hash,
    owner_ctx,
)
from tests.unit.domain.builders import VALID_SUMMARY

INTERNAL = Sensitivity.INTERNAL
PUBLIC = Sensitivity.PUBLIC
RESTRICTED = Sensitivity.RESTRICTED


@pytest.fixture
def kit() -> Kit:
    return Kit()


def edit_note(kit: Kit, **changes: Any) -> Note:
    """Apply an owner edit to the existing SSH note."""
    current = kit.world.notes.get(SSH)
    assert current is not None
    request = OwnerEditNoteRequest(note=replace(current, **changes), expected_hash=content_hash(current))
    kit.edit_uc().execute(request, owner_ctx())
    changed = kit.world.notes.get(SSH)
    assert changed is not None
    return changed


class TestRestrictedRaiseWarning:
    """An existing note rising to ``restricted`` keeps its old version in git history: the owner is told."""

    @staticmethod
    def free_ssh(kit: Kit, previous: Sensitivity = Sensitivity.INTERNAL) -> None:
        """Give ``server-ssh-access`` the ``previous`` level and no lower-sensitivity inbound link blocking a raise."""
        old_proxy = kit.world.notes.get(NID("old-proxy"))
        assert old_proxy is not None
        request = OwnerEditNoteRequest(
            note=replace(old_proxy, sensitivity=Sensitivity.RESTRICTED), expected_hash=content_hash(old_proxy)
        )
        assert kit.edit_uc().execute(request, owner_ctx()).revision is not None
        edit_note(kit, sensitivity=previous)

    @staticmethod
    def codes(result: Any) -> list[ApplyWarningCode]:
        return [w.code for w in result.warnings]

    @pytest.mark.parametrize("previous", [Sensitivity.PUBLIC, Sensitivity.INTERNAL])
    def test_owner_edit_warns_when_raising_to_restricted(self, kit: Kit, previous: Sensitivity) -> None:
        self.free_ssh(kit, previous)
        current = kit.world.notes.get(SSH)
        assert current is not None
        request = OwnerEditNoteRequest(
            note=replace(current, sensitivity=Sensitivity.RESTRICTED), expected_hash=content_hash(current)
        )
        result = kit.edit_uc().execute(request, owner_ctx())
        assert self.codes(result) == [ApplyWarningCode.PREVIOUS_VERSION_IN_HISTORY]

    def test_owner_edit_does_not_warn_otherwise(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        assert current.sensitivity is Sensitivity.INTERNAL  # fixture precondition
        for level in (Sensitivity.INTERNAL, Sensitivity.PUBLIC):  # unchanged, then lowered
            current = kit.world.notes.get(SSH)
            assert current is not None
            request = OwnerEditNoteRequest(
                note=replace(current, sensitivity=level), expected_hash=content_hash(current)
            )
            assert kit.edit_uc().execute(request, owner_ctx()).warnings == ()

    def test_owner_edit_with_errors_writes_nothing_and_does_not_warn(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        bad = replace(current, sensitivity=Sensitivity.RESTRICTED, body=f"key {SECRET}\n")
        result = kit.edit_uc().execute(OwnerEditNoteRequest(note=bad, expected_hash=content_hash(current)), owner_ctx())
        assert (result.revision, result.warnings) == (None, ())


# -- OwnerEditNote -------------------------------------------------------------------------------------------------


class TestOwnerEditNote:
    def edit(self, kit: Kit, note: Note, expected: str, c: Any = None) -> Any:
        return kit.edit_uc().execute(OwnerEditNoteRequest(note=note, expected_hash=expected), c or owner_ctx())

    def test_an_edit_always_names_the_loaded_version_of_a_note(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        with pytest.raises(InvalidValueError, match="expected_hash"):
            OwnerEditNoteRequest(note=current, expected_hash=None)  # type: ignore[arg-type]
        with pytest.raises(InvalidValueError, match="note"):
            OwnerEditNoteRequest(note="note", expected_hash=note_hash(kit))  # type: ignore[arg-type]

    def test_only_the_owner_edits(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        with pytest.raises(PermissionDenied):
            self.edit(kit, current, note_hash(kit), agent_ctx())
        assert kit.commits == 5

    def test_edit_commits_written_by_the_owner(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        result = self.edit(kit, replace(current, body="# SSH\n\nEdited.\n", created=date(2000, 1, 1)), note_hash(kit))
        assert result.revision is not None
        assert result.content_hash == note_hash(kit)
        note = kit.world.notes.get(SSH)
        assert note is not None
        assert (note.created, note.updated, note.body) == (date(2026, 11, 24), date(2026, 11, 25), "# SSH\n\nEdited.\n")
        message = kit.world.notes.commits[-1][1]
        assert message.render() == "Update note: server-ssh-access\n\nWritten-by: owner"
        assert [h.id for h in kit.world.search.search(_query("Edited"))] == [SSH]

    def test_stale_hash_is_a_conflict_and_writes_nothing(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        stale = note_hash(kit)
        edit_note(kit, body="# SSH\n\nSomeone else.\n")
        commits = kit.commits
        with pytest.raises(ContentConflict):
            self.edit(kit, replace(current, body="mine\n"), stale)
        assert kit.commits == commits

    def test_a_missing_note_is_not_found(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        ghost = replace(current, id=NID("ghost-note"))
        with pytest.raises(NoteNotFound):
            self.edit(kit, ghost, "a" * 64)

    @pytest.mark.parametrize("level", [PUBLIC, INTERNAL])
    def test_an_owner_below_restricted_edits_nothing(self, kit: Kit, level: Sensitivity) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        commits = kit.commits
        # Refused before the note is looked up: a visible note and an absent one answer alike.
        for note in (replace(current, body="Edited.\n"), replace(current, id=NID("ghost-note"))):
            with pytest.raises(PermissionDenied):
                self.edit(kit, note, note_hash(kit), owner_ctx(level))
        assert kit.commits == commits

    @pytest.mark.parametrize("where", ["title", "summary", "body"])
    def test_secrets_in_frontmatter_and_body_are_errors(self, kit: Kit, where: str) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        value = f"api_key = '{SECRET}' " + VALID_SUMMARY if where != "body" else f"token {SECRET}\n"
        edited: Note = {
            "title": replace(current, title=value),
            "summary": replace(current, summary=value),
            "body": replace(current, body=value),
        }[where]
        result = self.edit(kit, edited, note_hash(kit))
        assert result.revision is None
        assert ViolationCode.SECRET_DETECTED in result.validation.codes
        assert SECRET not in repr(result)
        assert kit.commits == 5

    def test_broken_links_only_warn_for_the_owner(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        result = self.edit(kit, replace(current, body="See [[not-written-yet]].\n"), note_hash(kit))
        assert result.revision is not None
        assert ViolationCode.BROKEN_LINK in {v.code for v in result.validation.warnings}

    @pytest.mark.parametrize("kind", ["body", "related", "superseded_by", "hub"])
    def test_links_to_a_higher_sensitivity_are_errors_for_the_owner_too(self, kit: Kit, kind: str) -> None:
        current = kit.world.notes.get(SSH)  # internal; vault-layout is restricted
        assert current is not None
        options: dict[str, dict[str, Any]] = {
            "body": {"body": "See [[vault-layout]].\n"},
            "related": {"related": (NID("vault-layout"),)},
            "superseded_by": {
                "superseded_by": NID("vault-layout"),
                "status": Status.SUPERSEDED,
                "priority": Priority.P3,
            },
            "hub": {"hub": NID("vault-layout")},
        }
        commits = kit.commits
        result = self.edit(kit, replace(current, **options[kind]), note_hash(kit))
        assert result.revision is None
        assert ViolationCode.LINK_TO_HIGHER_SENSITIVITY in {v.code for v in result.validation.errors}
        assert ViolationCode.LINK_TO_HIGHER_SENSITIVITY not in {v.code for v in result.validation.warnings}
        assert kit.commits == commits
        messages = [
            v.message for v in result.validation.violations if v.code is ViolationCode.LINK_TO_HIGHER_SENSITIVITY
        ]
        assert [m.endswith(" may not be linked from this note") for m in messages] == [True]
        assert "vault-layout" not in messages[0]

    @pytest.mark.parametrize("body", ["`[[vault-layout]]`", "[[vault-layout|the vault]]"])
    def test_alias_and_code_span_links_are_errors_for_the_owner(self, kit: Kit, body: str) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        result = self.edit(kit, replace(current, body=body + "\n"), note_hash(kit))
        assert result.revision is None
        assert ViolationCode.LINK_TO_HIGHER_SENSITIVITY in {v.code for v in result.validation.errors}

    def test_raising_sensitivity_is_refused_while_lower_notes_link_to_the_note(self, kit: Kit) -> None:
        # the write kit's data obeys the rule, so add a public note that links to the internal one
        kit.world.add(id=NID("linker"), hub=NID("infrastructure"), body="See [[server-ssh-access]].\n")
        current = kit.world.notes.get(SSH)  # internal
        assert current is not None
        commits = kit.commits
        result = self.edit(kit, replace(current, sensitivity=RESTRICTED), note_hash(kit))
        assert result.revision is None
        assert "linker links to this note" in [v.message for v in result.validation.errors]
        assert kit.commits == commits

    def test_an_edit_that_does_not_raise_sensitivity_is_not_blocked_by_existing_inbound_links(self, kit: Kit) -> None:
        kit.world.add(id=NID("linker"), hub=NID("infrastructure"), body="See [[server-ssh-access]].\n")
        current = kit.world.notes.get(SSH)  # internal, linked from the public "linker"
        assert current is not None
        unrelated = self.edit(kit, replace(current, body="# SSH\n\nEdited.\n"), note_hash(kit))
        assert unrelated.revision is not None
        again = kit.world.notes.get(SSH)
        assert again is not None
        # equal sensitivity and lowering are fine too
        assert self.edit(kit, replace(again, sensitivity=PUBLIC), note_hash(kit)).revision is not None

    def test_raising_a_hubs_sensitivity_is_refused_while_members_point_at_it(self, kit: Kit) -> None:
        hub = kit.world.notes.get(NID("infrastructure"))  # public hub with public/internal members
        assert hub is not None
        result = self.edit(kit, replace(hub, sensitivity=RESTRICTED), note_hash(kit, NID("infrastructure")))
        assert result.revision is None
        assert ViolationCode.LINK_TO_HIGHER_SENSITIVITY in {v.code for v in result.validation.errors}

    @pytest.mark.parametrize("field", ["title", "summary"])
    def test_wikilinks_in_title_or_summary_are_errors_for_the_owner(self, kit: Kit, field: str) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        value = "About [[core-rules]]" if field == "title" else VALID_SUMMARY + " [[core-rules]]"
        changes: dict[str, Any] = {field: value}
        result = self.edit(kit, replace(current, **changes), note_hash(kit))
        assert result.revision is None
        assert ViolationCode.WIKILINK_IN_METADATA in {v.code for v in result.validation.errors}

    def test_the_owner_may_link_to_equal_or_lower_sensitivity(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        result = self.edit(kit, replace(current, body="See [[core-rules]] and [[infrastructure]].\n"), note_hash(kit))
        assert result.revision is not None

    def test_invalid_notes_are_not_written(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        result = self.edit(kit, replace(current, summary="short"), note_hash(kit))
        assert result.revision is None
        assert ViolationCode.SUMMARY_LENGTH in result.validation.codes
        assert kit.commits == 5

    def test_the_new_sensitivity_may_not_exceed_the_ceiling(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        with pytest.raises(PermissionDenied):
            self.edit(kit, replace(current, sensitivity=RESTRICTED), note_hash(kit), owner_ctx(INTERNAL))
        assert kit.commits == 5

    def test_the_owner_may_lower_sensitivity(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        assert self.edit(kit, replace(current, sensitivity=PUBLIC), note_hash(kit)).revision is not None


# -- support -------------------------------------------------------------------------------------------------------


def _query(text: str) -> Any:
    from cerebro.application.ports.search import SearchQuery

    return SearchQuery(ceiling=owner_ctx().ceiling, text=text)


class TestIndexRevisionOnWrites:
    """The indexes record the revision they reflect; a failed index update after a commit is detectable."""

    @staticmethod
    def stale(kit: Kit) -> bool:
        w = kit.world
        return index_is_stale(w.notes, w.search, w.graph)

    def test_the_seeded_world_is_fresh(self, kit: Kit) -> None:
        assert not self.stale(kit)

    def test_owner_edit_advances_both_indexes_to_the_new_head(self, kit: Kit) -> None:
        current = kit.world.notes.get(SSH)
        assert current is not None
        result = kit.edit_uc().execute(
            OwnerEditNoteRequest(note=replace(current, body="# SSH\n\nEdited.\n"), expected_hash=note_hash(kit)),
            owner_ctx(),
        )
        assert result.revision == kit.world.notes.head()
        assert kit.world.search.built_from() == kit.world.graph.built_from() == result.revision

    @pytest.mark.parametrize("method", ["search.upsert", "graph.upsert", "search.set_revision", "graph.set_revision"])
    def test_a_failed_index_update_after_owner_edit_is_flagged_not_raised(self, kit: Kit, method: str) -> None:
        """The commit is durable, so the edit succeeds; the index stays behind ``head()`` for the next read."""
        current = kit.world.notes.get(SSH)
        assert current is not None

        def boom(*args: Any) -> None:
            raise RuntimeError("index store down")

        index, name = method.split(".")
        setattr(getattr(kit.world, index), name, boom)
        result = kit.edit_uc().execute(
            OwnerEditNoteRequest(note=replace(current, body="# SSH\n\nEdited.\n"), expected_hash=note_hash(kit)),
            owner_ctx(),
        )
        assert result.revision == kit.world.notes.head()
        assert self.stale(kit)
