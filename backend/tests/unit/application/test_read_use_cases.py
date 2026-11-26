from dataclasses import fields, replace

import pytest

from cerebro.application.dto import (
    GetGraphRequest,
    GetNeighborsRequest,
    GetOverviewRequest,
    NoteView,
    ReadNoteRequest,
    SearchNotesRequest,
)
from cerebro.application.errors import NoteNotFound, NoteReadError, PermissionDenied, SectionNotFound
from cerebro.application.use_cases import GetGraph, GetNeighbors, GetOverview, ReadNote, SearchNotes
from cerebro.domain.access import AccessCeiling, Scope
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.links import REDACTED_LINK, LinkKind
from cerebro.domain.vocabulary import CORE, HUB, Area
from tests.unit.application.world import NID, World, ctx, standard_world

PUBLIC = Sensitivity.PUBLIC
INTERNAL = Sensitivity.INTERNAL
RESTRICTED = Sensitivity.RESTRICTED
NO_SCOPE = frozenset[Scope]()
WRITE_ONLY = frozenset({Scope.WRITE})


@pytest.fixture
def world() -> World:
    return standard_world()


def all_executions(w: World) -> dict[str, object]:
    """Every read use case, as a callable taking a context, for the scope tests."""
    return {
        "overview": lambda c: GetOverview(w.notes, w.search).execute(GetOverviewRequest(), c),
        "search": lambda c: SearchNotes(w.search).execute(SearchNotesRequest(), c),
        "read": lambda c: ReadNote(w.notes, w.graph).execute(ReadNoteRequest(note_id=NID("core-rules")), c),
        "neighbors": lambda c: GetNeighbors(w.notes, w.graph).execute(
            GetNeighborsRequest(note_id=NID("core-rules")), c
        ),
        "graph": lambda c: GetGraph(w.graph).execute(GetGraphRequest(), c),
    }


class TestScopes:
    @pytest.mark.parametrize("scopes", [NO_SCOPE, WRITE_ONLY])
    @pytest.mark.parametrize("name", ["overview", "search", "read", "neighbors", "graph"])
    def test_read_scope_is_required(self, world: World, name: str, scopes: frozenset[Scope]) -> None:
        run = all_executions(world)[name]
        with pytest.raises(PermissionDenied) as error:
            run(ctx(RESTRICTED, scopes))  # type: ignore[operator]
        assert error.value.action == "read"

    def test_scope_is_checked_before_anything_is_looked_up(self, world: World) -> None:
        # No scope and a missing note: the caller learns about the scope, not about the note.
        with pytest.raises(PermissionDenied):
            ReadNote(world.notes, world.graph).execute(ReadNoteRequest(note_id=NID("nothing")), ctx(scopes=NO_SCOPE))

    @pytest.mark.parametrize("name", ["overview", "search", "read", "neighbors", "graph"])
    def test_agent_with_read_and_owner_are_served(self, world: World, name: str) -> None:
        run = all_executions(world)[name]
        run(ctx(INTERNAL))  # type: ignore[operator]
        run(ctx(INTERNAL, owner=True))  # type: ignore[operator]


class TestGetOverview:
    def test_serves_current_p0_notes_hubs_and_areas(self, world: World) -> None:
        overview = GetOverview(world.notes, world.search).execute(GetOverviewRequest(), ctx())
        assert [c.id for c in overview.core] == [NID("core-rules")]
        assert overview.core[0].body == "Always be kind. See [[server-ssh-access]].\n"
        assert overview.core_words == 5
        assert not overview.core_truncated
        assert [h.id for h in overview.hubs] == [NID("infrastructure")]
        assert [str(a) for a in overview.areas] == ["infrastructure"]

    def test_hides_p0_notes_above_the_ceiling_and_history(self, world: World) -> None:
        world.add(id=NID("core-internal"), type=CORE, priority=Priority.P0, sensitivity=INTERNAL, body="Internal.")
        world.add(id=NID("core-old"), type=CORE, priority=Priority.P0, status=world.notes.get(NID("old-proxy")).status)  # type: ignore[union-attr]
        public = GetOverview(world.notes, world.search).execute(GetOverviewRequest(), ctx(PUBLIC))
        internal = GetOverview(world.notes, world.search).execute(GetOverviewRequest(), ctx(INTERNAL))
        assert [c.id.value for c in public.core] == ["core-rules"]
        assert [c.id.value for c in internal.core] == ["core-internal", "core-rules"]

    def test_hubs_above_the_ceiling_are_not_listed(self, world: World) -> None:
        world.add(
            id=NID("secret-hub"),
            type=HUB,
            area=Area("secret-area"),
            sensitivity=RESTRICTED,
        )
        low = GetOverview(world.notes, world.search).execute(GetOverviewRequest(), ctx(INTERNAL))
        high = GetOverview(world.notes, world.search).execute(GetOverviewRequest(), ctx(RESTRICTED))
        assert [h.id.value for h in low.hubs] == ["infrastructure"]
        assert [str(a) for a in low.areas] == ["infrastructure"]
        assert [h.id.value for h in high.hubs] == ["infrastructure", "secret-hub"]
        assert [str(a) for a in high.areas] == ["infrastructure", "secret-area"]

    def test_stops_at_the_core_budget(self, world: World) -> None:
        world.add(id=NID("core-a"), type=CORE, priority=Priority.P0, body=" ".join(["w"] * 800))
        world.add(id=NID("core-b"), type=CORE, priority=Priority.P0, body=" ".join(["w"] * 200))
        overview = GetOverview(world.notes, world.search).execute(GetOverviewRequest(), ctx())
        assert [c.id.value for c in overview.core] == ["core-a"]
        assert overview.core_words == 800
        assert overview.core_truncated

    def test_trusts_the_repository_over_a_stale_index(self, world: World) -> None:
        # The index still says P0/public; the stored note is now restricted: it must not be served.
        stale = world.notes.get(NID("core-rules"))
        assert stale is not None
        world.notes._notes[stale.id] = replace(stale, sensitivity=RESTRICTED)
        overview = GetOverview(world.notes, world.search).execute(GetOverviewRequest(), ctx(PUBLIC))
        assert overview.core == ()

    def test_a_note_demoted_from_p0_is_not_served_as_core(self, world: World) -> None:
        stored = world.notes.get(NID("core-rules"))
        assert stored is not None
        world.notes._notes[stored.id] = replace(stored, priority=Priority.P2)
        overview = GetOverview(world.notes, world.search).execute(GetOverviewRequest(), ctx(PUBLIC))
        assert overview.core == ()


class TestSearchNotes:
    def test_returns_summaries_without_body_or_sensitivity(self, world: World) -> None:
        found = SearchNotes(world.search).execute(SearchNotesRequest(text="ssh"), ctx())
        assert found.results[0].note.id == NID("server-ssh-access")
        shown = found.results[0].note
        assert not hasattr(shown, "body")
        assert not hasattr(shown, "sensitivity")
        assert found.results[0].score > 0

    def test_hidden_notes_are_absent_whatever_the_query(self, world: World) -> None:
        by_text = SearchNotes(world.search).execute(SearchNotesRequest(text="vault secret layout"), ctx(INTERNAL))
        everything = SearchNotes(world.search).execute(SearchNotesRequest(), ctx(INTERNAL))
        by_filter = SearchNotes(world.search).execute(
            SearchNotesRequest(statuses=frozenset({world.notes.get(NID("vault-layout")).status})),  # type: ignore[union-attr]
            ctx(INTERNAL),
        )
        for result in (by_text, everything, by_filter):
            assert NID("vault-layout") not in [r.note.id for r in result.results]
        assert by_text.results == ()  # nothing else matches: identical to a query matching nothing
        assert by_text == SearchNotes(world.search).execute(SearchNotesRequest(text="zzzz"), ctx(INTERNAL))

    def test_visible_with_a_higher_ceiling(self, world: World) -> None:
        found = SearchNotes(world.search).execute(SearchNotesRequest(text="vault"), ctx(RESTRICTED))
        assert [r.note.id.value for r in found.results] == ["vault-layout"]

    def test_history_needs_include_history(self, world: World) -> None:
        without = SearchNotes(world.search).execute(SearchNotesRequest(text="proxy"), ctx())
        with_history = SearchNotes(world.search).execute(SearchNotesRequest(text="proxy", include_history=True), ctx())
        assert without.results == ()
        assert [r.note.id.value for r in with_history.results] == ["old-proxy"]

    def test_include_history_does_not_raise_the_ceiling(self, world: World) -> None:
        found = SearchNotes(world.search).execute(SearchNotesRequest(include_history=True), ctx(INTERNAL))
        assert NID("vault-layout") not in [r.note.id for r in found.results]

    def test_filters_and_limit(self, world: World) -> None:
        only_hubs = SearchNotes(world.search).execute(
            SearchNotesRequest(types=frozenset({world.notes.get(NID("infrastructure")).type})),  # type: ignore[union-attr]
            ctx(),
        )
        assert [r.note.id.value for r in only_hubs.results] == ["infrastructure"]
        limited = SearchNotes(world.search).execute(SearchNotesRequest(limit=1), ctx())
        assert len(limited.results) == 1

    @pytest.mark.parametrize("limit", [0, 101, True, 1.5])
    def test_request_validates_limit(self, limit: object) -> None:
        with pytest.raises(InvalidValueError):
            SearchNotesRequest(limit=limit)  # type: ignore[arg-type]

    def test_request_validates_text_and_flag(self) -> None:
        with pytest.raises(InvalidValueError):
            SearchNotesRequest(text=1)  # type: ignore[arg-type]
        with pytest.raises(InvalidValueError):
            SearchNotesRequest(include_history="yes")  # type: ignore[arg-type]


class TestReadNote:
    def test_only_authenticated_writer_gets_base_on_full_read(self, world: World) -> None:
        read = ReadNote(world.notes, world.graph)
        request = ReadNoteRequest(note_id=NID("infrastructure"))
        writer = replace(ctx(scopes=frozenset({Scope.READ, Scope.WRITE})), token_id="writer")
        complete = read.execute(request, writer)
        assert complete.base_hash == complete.content_hash
        for other in (ctx(owner=True), replace(writer, token_id=None), replace(ctx(), token_id="reader")):
            assert read.execute(request, other).base_hash is None
        section = ReadNoteRequest(note_id=NID("server-ssh-access"), section="SSH")
        assert read.execute(section, writer).base_hash is None

    @pytest.mark.parametrize("field", ["body", "title", "summary", "related", "hub", "superseded_by"])
    def test_filtered_field_withholds_base_hash(self, field: str, world: World) -> None:
        target = NID("vault-layout")  # restricted: above the writer's internal ceiling
        value: object = (target,) if field == "related" else target
        if field in {"title", "summary", "body"}:
            value = "Legacy text mentioning `[[vault-layout|alias]]`."
        note = world.add(id=NID("legacy-note"), **{field: value})
        read = ReadNote(world.notes, world.graph)
        writer = replace(ctx(scopes=frozenset({Scope.READ, Scope.WRITE})), token_id="writer")
        request = ReadNoteRequest(note_id=note.id)
        assert read.execute(request, writer).base_hash is None
        assert read.execute(replace(request, include_history=True), writer).base_hash is None
        full = read.execute(request, replace(writer, ceiling=AccessCeiling(RESTRICTED)))
        assert full.base_hash == full.content_hash

    @pytest.mark.parametrize("field", ["body", "related", "superseded_by"])
    def test_references_to_readable_history_keep_the_base_hash(self, field: str, world: World) -> None:
        target = NID("old-proxy")  # superseded, internal
        value: object = (target,) if field == "related" else target
        if field == "body":
            value = "Replaces [[old-proxy#Setup|the old proxy]].\n"
        note = world.add(id=NID("successor-note"), **{field: value})
        writer = replace(ctx(scopes=frozenset({Scope.READ, Scope.WRITE})), token_id="writer")
        view = ReadNote(world.notes, world.graph).execute(ReadNoteRequest(note_id=note.id), writer)
        assert view.base_hash == view.content_hash == view.visible_content_hash
        assert getattr(view, field) == getattr(note, field)

    def test_reads_a_note(self, world: World) -> None:
        view = ReadNote(world.notes, world.graph).execute(ReadNoteRequest(note_id=NID("server-ssh-access")), ctx())
        assert view.id == NID("server-ssh-access")
        assert view.body.startswith("# SSH")
        assert view.section is None
        assert view.members is None
        assert len(view.content_hash) == 64
        assert not hasattr(view, "__dict__")

    def test_reads_one_section_with_its_subsections(self, world: World) -> None:
        view = ReadNote(world.notes, world.graph).execute(
            ReadNoteRequest(note_id=NID("server-ssh-access"), section="ssh"), ctx()
        )
        assert view.body == "# SSH\n\nUse the key.\n\n## Verify\n\nCheck the host.\n"
        assert view.section == "ssh"
        whole = ReadNote(world.notes, world.graph).execute(ReadNoteRequest(note_id=NID("server-ssh-access")), ctx())
        assert view.content_hash == whole.content_hash

    def test_reads_by_wikilink(self, world: World) -> None:
        request = ReadNoteRequest.parse("[[server-ssh-access#Verify]]")
        assert request.note_id == NID("server-ssh-access")
        assert request.section == "Verify"
        view = ReadNote(world.notes, world.graph).execute(request, ctx())
        assert view.body == "## Verify\n\nCheck the host.\n"

    @pytest.mark.parametrize(
        ("reference", "section"),
        [("a-note", None), ("[[a-note]]", None), (" a-note#Part two ", "Part two"), ("[[a-note#]]", None)],
    )
    def test_parse_forms(self, reference: str, section: str | None) -> None:
        request = ReadNoteRequest.parse(reference, include_history=True)
        assert (request.note_id, request.section, request.include_history) == (NID("a-note"), section, True)

    @pytest.mark.parametrize("reference", ["", "[[Not A Slug]]", "[[#Section]]", 5])
    def test_parse_rejects_bad_references(self, reference: object) -> None:
        with pytest.raises(InvalidValueError):
            ReadNoteRequest.parse(reference)  # type: ignore[arg-type]

    def test_request_validation(self) -> None:
        with pytest.raises(InvalidValueError):
            ReadNoteRequest(note_id="a-note")  # type: ignore[arg-type]
        with pytest.raises(InvalidValueError):
            ReadNoteRequest(note_id=NID("a-note"), section="  ")
        with pytest.raises(InvalidValueError):
            ReadNoteRequest(note_id=NID("a-note"), include_history=1)  # type: ignore[arg-type]

    def test_unknown_section_is_reported_for_a_visible_note(self, world: World) -> None:
        with pytest.raises(SectionNotFound) as error:
            ReadNote(world.notes, world.graph).execute(
                ReadNoteRequest(note_id=NID("server-ssh-access"), section="Nope"), ctx()
            )
        assert error.value.section == "Nope"
        assert error.value.headings == ("SSH", "Verify", "Other")
        assert error.value.headings_truncated is False

    def test_listed_headings_are_redacted_and_can_be_requested(self, world: World) -> None:
        world.add(id=NID("with-links"), body="## See [[vault-layout]]\n\nText.\n```\n## Not a heading\n```\n")
        read = ReadNote(world.notes, world.graph)
        with pytest.raises(SectionNotFound) as error:
            read.execute(ReadNoteRequest(note_id=NID("with-links"), section="Nope"), ctx(INTERNAL))
        assert error.value.headings == (f"See {REDACTED_LINK}",)
        # A hidden id cannot be confirmed by naming it in a heading request.
        with pytest.raises(SectionNotFound):
            read.execute(ReadNoteRequest(note_id=NID("with-links"), section="See [[vault-layout]]"), ctx(INTERNAL))
        view = read.execute(ReadNoteRequest(note_id=NID("with-links"), section=error.value.headings[0]), ctx(INTERNAL))
        assert "Text." in view.body

    @pytest.mark.parametrize(
        ("body", "listed"),
        [
            ("".join(f"## Part {n}\n" for n in range(100)), 40),
            ("".join(f"## {n} {'x' * 200}\n" for n in range(40)), 10),
            (f"## {'é' * 200}\n## Short\n## Short\n## \n", 1),
        ],
        ids=["count", "total-bytes", "unrequestable-and-repeated"],
    )
    def test_listed_headings_are_bounded(self, world: World, body: str, listed: int) -> None:
        world.add(id=NID("long-note"), body=body)
        with pytest.raises(SectionNotFound) as error:
            ReadNote(world.notes, world.graph).execute(
                ReadNoteRequest(note_id=NID("long-note"), section="Nope"), ctx(INTERNAL)
            )
        assert len(error.value.headings) == listed
        assert sum(len(h.encode()) for h in error.value.headings) <= 2048
        assert error.value.headings_truncated is True

    def test_hidden_and_missing_notes_fail_identically(self, world: World) -> None:
        read = ReadNote(world.notes, world.graph)
        with pytest.raises(NoteNotFound) as hidden:
            read.execute(ReadNoteRequest(note_id=NID("vault-layout")), ctx(INTERNAL))
        with pytest.raises(NoteNotFound) as missing:
            read.execute(ReadNoteRequest(note_id=NID("vault-layoutx")), ctx(INTERNAL))
        assert type(hidden.value) is type(missing.value)
        assert str(hidden.value).replace("vault-layout", "X") == str(missing.value).replace("vault-layoutx", "X")
        assert hidden.value.args[0].replace("vault-layout", "X") == missing.value.args[0].replace("vault-layoutx", "X")

    def test_a_hidden_section_request_does_not_reveal_the_note(self, world: World) -> None:
        # Asking for a section of a hidden note fails as "note not found", never as "section not found".
        with pytest.raises(NoteNotFound):
            ReadNote(world.notes, world.graph).execute(
                ReadNoteRequest(note_id=NID("vault-layout"), section="Whatever"), ctx(INTERNAL)
            )

    @pytest.mark.parametrize("include_history", [False, True])
    def test_history_is_readable_by_id_with_its_status(self, world: World, include_history: bool) -> None:
        request = ReadNoteRequest(note_id=NID("old-proxy"), include_history=include_history)
        view = ReadNote(world.notes, world.graph).execute(request, ctx())
        assert view.id == NID("old-proxy")
        assert not view.status.is_current

    def test_hidden_history_fails_like_a_missing_note(self, world: World) -> None:
        world.add(id=NID("old-vault"), sensitivity=RESTRICTED, status=Status.ARCHIVED, priority=Priority.P3)
        read = ReadNote(world.notes, world.graph)
        messages = []
        for note_id in ("old-vault", "old-vaultx"):
            with pytest.raises(NoteNotFound) as error:
                read.execute(ReadNoteRequest(note_id=NID(note_id)), ctx(INTERNAL))
            messages.append(str(error.value).replace(note_id, "X"))
        assert messages[0] == messages[1]

    def test_include_history_does_not_raise_the_ceiling(self, world: World) -> None:
        with pytest.raises(NoteNotFound):
            ReadNote(world.notes, world.graph).execute(
                ReadNoteRequest(note_id=NID("vault-layout"), include_history=True), ctx(INTERNAL)
            )

    def test_restricted_note_is_readable_with_a_restricted_ceiling(self, world: World) -> None:
        view = ReadNote(world.notes, world.graph).execute(ReadNoteRequest(note_id=NID("vault-layout")), ctx(RESTRICTED))
        assert view.sensitivity is RESTRICTED


class TestHubMembers:
    def read_hub(self, w: World, c: object, *, history: bool = False):  # type: ignore[no-untyped-def]
        return ReadNote(w.notes, w.graph).execute(
            ReadNoteRequest(note_id=NID("infrastructure"), include_history=history),
            c,  # type: ignore[arg-type]
        )

    def test_members_are_built_at_read_time_from_the_hub_field(self, world: World) -> None:
        assert [m.id.value for m in self.read_hub(world, ctx(INTERNAL)).members or ()] == ["server-ssh-access"]
        world.add(id=NID("new-note"), title="Aaa new", hub=NID("infrastructure"), priority=Priority.P1)
        members = self.read_hub(world, ctx(INTERNAL)).members or ()
        # Ordered by priority, then title.
        assert [m.id.value for m in members] == ["new-note", "server-ssh-access"]

    def test_members_above_the_ceiling_are_invisible(self, world: World) -> None:
        internal = self.read_hub(world, ctx(INTERNAL))
        restricted = self.read_hub(world, ctx(RESTRICTED))
        assert [m.id.value for m in internal.members or ()] == ["server-ssh-access"]
        assert [m.id.value for m in restricted.members or ()] == ["server-ssh-access", "vault-layout"]
        assert "vault" not in repr(internal).lower()

    def test_hub_with_only_hidden_members_looks_empty(self) -> None:
        w = World()
        w.add(id=NID("lonely-hub"), type=HUB, priority=Priority.P1, body="Intro.")
        w.add(id=NID("hidden-child"), hub=NID("lonely-hub"), sensitivity=RESTRICTED)
        read = ReadNote(w.notes, w.graph)
        none_visible = read.execute(ReadNoteRequest(note_id=NID("lonely-hub")), ctx(INTERNAL))
        assert none_visible.members == ()
        assert [
            m.id.value for m in read.execute(ReadNoteRequest(note_id=NID("lonely-hub")), ctx(RESTRICTED)).members or ()
        ] == ["hidden-child"]

    def test_history_members_need_include_history(self, world: World) -> None:
        assert "old-proxy" not in [m.id.value for m in self.read_hub(world, ctx()).members or ()]
        with_history = self.read_hub(world, ctx(), history=True).members or ()
        assert "old-proxy" in [m.id.value for m in with_history]

    def test_member_entries_have_no_body(self, world: World) -> None:
        members = self.read_hub(world, ctx(INTERNAL)).members
        assert members
        member = members[0]
        assert {f.name for f in fields(member)} == {"id", "title", "type", "priority", "status", "summary"}

    def test_sub_hub_is_listed_in_its_parent(self) -> None:
        w = World()
        w.add(id=NID("area-hub"), type=HUB, priority=Priority.P1)
        w.add(id=NID("sub-hub"), type=HUB, priority=Priority.P1, hub=NID("area-hub"))
        w.add(id=NID("leaf"), hub=NID("sub-hub"))
        read = ReadNote(w.notes, w.graph)
        parent = read.execute(ReadNoteRequest(note_id=NID("area-hub")), ctx())
        assert [m.id.value for m in parent.members or ()] == ["sub-hub"]

    def test_a_note_that_stopped_pointing_at_the_hub_is_dropped(self, world: World) -> None:
        # The graph index is stale (still has the hub edge) but the stored note moved: the repository wins.
        moved = world.notes.get(NID("server-ssh-access"))
        assert moved is not None
        world.notes._notes[moved.id] = replace(moved, hub=None)
        assert (self.read_hub(world, ctx(INTERNAL)).members or ()) == ()


class TestGetNeighbors:
    def test_returns_the_neighborhood(self, world: World) -> None:
        view = GetNeighbors(world.notes, world.graph).execute(
            GetNeighborsRequest(note_id=NID("server-ssh-access")), ctx(INTERNAL)
        )
        assert view.center == NID("server-ssh-access")
        assert [n.id.value for n in view.nodes] == ["core-rules", "infrastructure", "server-ssh-access"]
        kinds = {(e.source.value, e.target.value, e.kind) for e in view.edges}
        assert ("core-rules", "server-ssh-access", LinkKind.BODY) in kinds
        assert ("server-ssh-access", "infrastructure", LinkKind.HUB) in kinds

    def test_hidden_notes_are_not_nodes_edges_or_a_path(self, world: World) -> None:
        view = GetNeighbors(world.notes, world.graph).execute(
            GetNeighborsRequest(note_id=NID("server-ssh-access"), hops=3), ctx(INTERNAL)
        )
        ids = {n.id for n in view.nodes} | {e.source for e in view.edges} | {e.target for e in view.edges}
        assert NID("vault-layout") not in ids
        assert NID("old-proxy") not in ids
        assert "vault" not in repr(view)

    def test_hidden_center_fails_like_a_missing_one(self, world: World) -> None:
        uc = GetNeighbors(world.notes, world.graph)
        with pytest.raises(NoteNotFound) as hidden:
            uc.execute(GetNeighborsRequest(note_id=NID("vault-layout")), ctx(INTERNAL))
        with pytest.raises(NoteNotFound) as missing:
            uc.execute(GetNeighborsRequest(note_id=NID("vault-layoutx")), ctx(INTERNAL))
        assert type(hidden.value) is type(missing.value)
        assert str(hidden.value).replace("vault-layout", "X") == str(missing.value).replace("vault-layoutx", "X")

    def test_higher_ceiling_sees_more(self, world: World) -> None:
        view = GetNeighbors(world.notes, world.graph).execute(
            GetNeighborsRequest(note_id=NID("server-ssh-access")), ctx(RESTRICTED)
        )
        assert NID("vault-layout") in {n.id for n in view.nodes}

    def test_history_needs_include_history(self, world: World) -> None:
        uc = GetNeighbors(world.notes, world.graph)
        with pytest.raises(NoteNotFound):
            uc.execute(GetNeighborsRequest(note_id=NID("old-proxy")), ctx())
        view = uc.execute(GetNeighborsRequest(note_id=NID("old-proxy"), include_history=True), ctx())
        assert NID("server-ssh-access") in {n.id for n in view.nodes}

    @pytest.mark.parametrize("hops", [0, 6, True, "1"])
    def test_request_validates_hops(self, hops: object) -> None:
        with pytest.raises(InvalidValueError):
            GetNeighborsRequest(note_id=NID("a-note"), hops=hops)  # type: ignore[arg-type]

    def test_request_validates_other_fields(self) -> None:
        with pytest.raises(InvalidValueError):
            GetNeighborsRequest(note_id="a-note")  # type: ignore[arg-type]
        with pytest.raises(InvalidValueError):
            GetNeighborsRequest(note_id=NID("a-note"), include_history=None)  # type: ignore[arg-type]


class TestGetGraph:
    def test_lists_visible_nodes_and_edges_only(self, world: World) -> None:
        view = GetGraph(world.graph).execute(GetGraphRequest(), ctx(INTERNAL))
        assert view.center is None
        assert [n.id.value for n in view.nodes] == ["core-rules", "infrastructure", "server-ssh-access"]
        ends = {e.source for e in view.edges} | {e.target for e in view.edges}
        assert ends <= {n.id for n in view.nodes}
        assert "vault" not in repr(view)

    def test_a_hidden_note_leaves_no_trace_in_counts(self, world: World) -> None:
        low = GetGraph(world.graph).execute(GetGraphRequest(), ctx(INTERNAL))
        # Same world without the hidden notes at all: the answers must be identical.
        clean = standard_world()
        for gone in ("vault-layout",):
            clean.graph.remove(NID(gone))
        assert GetGraph(clean.graph).execute(GetGraphRequest(), ctx(INTERNAL)) == low

    def test_more_with_a_higher_ceiling_and_with_history(self, world: World) -> None:
        graph = GetGraph(world.graph)
        assert len(graph.execute(GetGraphRequest(), ctx(RESTRICTED)).nodes) == 4
        assert len(graph.execute(GetGraphRequest(include_history=True), ctx(RESTRICTED)).nodes) == 5
        assert len(graph.execute(GetGraphRequest(include_history=True), ctx(INTERNAL)).nodes) == 4

    def test_request_validates_flag(self) -> None:
        with pytest.raises(InvalidValueError):
            GetGraphRequest(include_history="no")  # type: ignore[arg-type]

    def test_nodes_carry_display_attributes_only(self, world: World) -> None:
        node = GetGraph(world.graph).execute(GetGraphRequest(), ctx()).nodes[0]
        assert {f.name for f in fields(node)} == {
            "id",
            "title",
            "summary",
            "type",
            "area",
            "priority",
            "status",
            "sensitivity",
        }
        source = world.notes.get(node.id)
        assert source is not None
        assert (node.area, node.sensitivity) == (source.area, source.sensitivity)


class TestNoLeakAcrossUseCases:
    """Hidden-note facts must not change any answer visible to a lower ceiling."""

    def test_removing_hidden_notes_changes_nothing_for_a_low_ceiling(self, world: World) -> None:
        low = ctx(INTERNAL)
        without = standard_world()
        for gone in ("vault-layout",):
            without.notes._notes.pop(NID(gone))
            without.search.remove(NID(gone))
            without.graph.remove(NID(gone))

        def snapshot(w: World) -> list[object]:
            return [
                GetOverview(w.notes, w.search).execute(GetOverviewRequest(), low),
                SearchNotes(w.search).execute(SearchNotesRequest(), low),
                SearchNotes(w.search).execute(SearchNotesRequest(text="layout"), low),
                ReadNote(w.notes, w.graph).execute(ReadNoteRequest(note_id=NID("infrastructure")), low),
                GetNeighbors(w.notes, w.graph).execute(GetNeighborsRequest(note_id=NID("infrastructure"), hops=3), low),
                GetGraph(w.graph).execute(GetGraphRequest(), low),
            ]

        assert snapshot(world) == snapshot(without)


def linking_world() -> World:
    """Notes whose bodies link to a restricted note, a visible note and a missing one (legacy data)."""
    w = standard_world()
    w.add(
        id=NID("daily-notes"),
        title="Daily notes",
        hub=NID("infrastructure"),
        sensitivity=INTERNAL,
        body=(
            "# Intro\n\nSee [[vault-layout]], [[core-rules]] and [[ghost-note]].\n\n"
            "# Details\n\nAlso [[vault-layout#Secrets]] and [[ vault-layout | the vault ]].\n"
        ),
    )
    return w


class TestWikilinkRedaction:
    @staticmethod
    def read(w: World, level: Sensitivity, section: str | None = None, **kw: object) -> NoteView:
        request = ReadNoteRequest(note_id=NID("daily-notes"), section=section, **kw)  # type: ignore[arg-type]
        return ReadNote(w.notes, w.graph).execute(request, ctx(level))

    def test_links_above_the_ceiling_or_missing_become_the_marker(self) -> None:
        view = self.read(linking_world(), INTERNAL)
        assert view.body.count(REDACTED_LINK) == 4  # three vault-layout forms and the missing ghost-note
        assert "[[core-rules]]" in view.body
        assert "vault-layout" not in repr(view)
        assert "ghost-note" not in repr(view)

    def test_sections_get_the_same_treatment(self) -> None:
        view = self.read(linking_world(), INTERNAL, section="Details")
        assert view.body == f"# Details\n\nAlso {REDACTED_LINK} and {REDACTED_LINK}.\n"
        assert "vault-layout" not in repr(view)

    def test_a_link_to_a_visible_note_is_untouched(self) -> None:
        view = self.read(linking_world(), INTERNAL, section="Intro")
        assert "[[core-rules]]" in view.body

    def test_a_reader_whose_ceiling_covers_the_target_sees_it(self) -> None:
        view = self.read(linking_world(), RESTRICTED)
        assert view.body.count(REDACTED_LINK) == 1  # only the missing ghost-note
        assert "[[vault-layout]]" in view.body
        assert "[[vault-layout#Secrets]]" in view.body

    def test_the_owner_with_a_restricted_ceiling_sees_it(self) -> None:
        request = ReadNoteRequest(note_id=NID("daily-notes"))
        view = ReadNote(linking_world().notes, linking_world().graph).execute(request, ctx(RESTRICTED, owner=True))
        assert "[[vault-layout]]" in view.body

    def test_the_owner_with_a_lower_ceiling_is_redacted_too(self) -> None:
        request = ReadNoteRequest(note_id=NID("daily-notes"))
        w = linking_world()
        view = ReadNote(w.notes, w.graph).execute(request, ctx(INTERNAL, owner=True))
        assert "vault-layout" not in repr(view)

    def test_history_targets_within_the_ceiling_are_not_redacted(self) -> None:
        w = standard_world()
        w.add(id=NID("old-vault"), sensitivity=RESTRICTED, status=Status.ARCHIVED, priority=Priority.P3)
        w.add(id=NID("pointer"), hub=NID("infrastructure"), body="Was [[old-proxy]], not [[old-vault]].\n")
        view = ReadNote(w.notes, w.graph).execute(ReadNoteRequest(note_id=NID("pointer")), ctx(INTERNAL))
        assert view.body == f"Was [[old-proxy]], not {REDACTED_LINK}.\n"

    def test_non_ids_and_ordinary_text_are_left_alone(self) -> None:
        w = standard_world()
        w.add(id=NID("plain"), hub=NID("infrastructure"), body="Text [[Not A Slug]] and [x](y) and [[a]\n")
        view = ReadNote(w.notes, w.graph).execute(ReadNoteRequest(note_id=NID("plain")), ctx(INTERNAL))
        assert view.body == "Text [[Not A Slug]] and [x](y) and [[a]\n"

    def test_hub_and_superseded_by_references_above_the_ceiling_are_dropped(self) -> None:
        w = standard_world()
        w.add(id=NID("secret-hub"), type=HUB, priority=Priority.P1, sensitivity=RESTRICTED, hub=None)
        w.add(
            id=NID("leaky"),
            hub=NID("secret-hub"),
            sensitivity=INTERNAL,
            status=Status.SUPERSEDED,
            priority=Priority.P3,
            superseded_by=NID("vault-layout"),
            related=(NID("vault-layout"), NID("core-rules")),
        )
        view = ReadNote(w.notes, w.graph).execute(
            ReadNoteRequest(note_id=NID("leaky"), include_history=True), ctx(INTERNAL)
        )
        assert (view.hub, view.superseded_by, view.related) == (None, None, (NID("core-rules"),))
        assert "vault-layout" not in repr(view)
        assert "secret-hub" not in repr(view)

    def test_hub_members_never_include_notes_above_the_ceiling(self) -> None:
        w = standard_world()
        view = ReadNote(w.notes, w.graph).execute(ReadNoteRequest(note_id=NID("infrastructure")), ctx(INTERNAL))
        assert NID("vault-layout") not in [m.id for m in view.members or ()]
        assert "vault-layout" not in repr(view)

    def test_the_overview_redacts_core_bodies_too(self) -> None:
        w = standard_world()  # core-rules (public) links the internal server-ssh-access
        public = GetOverview(w.notes, w.search).execute(GetOverviewRequest(), ctx(PUBLIC))
        assert public.core[0].body == f"Always be kind. See {REDACTED_LINK}.\n"
        assert "server-ssh-access" not in repr(public)
        internal = GetOverview(w.notes, w.search).execute(GetOverviewRequest(), ctx(INTERNAL))
        assert "[[server-ssh-access]]" in internal.core[0].body

    def test_the_overview_keeps_links_to_readable_history_but_serves_only_current_core(self) -> None:
        w = standard_world()
        w.add(id=NID("old-vault"), sensitivity=RESTRICTED, status=Status.ARCHIVED, priority=Priority.P3)
        w.add(id=NID("core-old"), type=CORE, priority=Priority.P0, hub=None, status=Status.ARCHIVED)
        w.add(
            id=NID("core-links"), type=CORE, priority=Priority.P0, hub=None, body="See [[old-proxy]] [[old-vault]].\n"
        )
        overview = GetOverview(w.notes, w.search).execute(GetOverviewRequest(), ctx(INTERNAL))
        bodies = {note.id.value: note.body for note in overview.core}
        assert "core-old" not in bodies
        assert bodies["core-links"] == f"See [[old-proxy]] {REDACTED_LINK}.\n"

    def test_the_marker_is_not_a_valid_note_id(self) -> None:
        from cerebro.domain.links import extract_wikilinks

        assert extract_wikilinks(REDACTED_LINK) == ()


class TestEchoedReferencesAndFaultyIndexes:
    def test_references_to_hidden_notes_are_not_echoed(self, world: World) -> None:
        # server-ssh-access (internal) is related to vault-layout (restricted)
        low = ReadNote(world.notes, world.graph).execute(
            ReadNoteRequest(note_id=NID("server-ssh-access")), ctx(INTERNAL)
        )
        assert low.related == ()
        assert "vault" not in repr(low)
        high = ReadNote(world.notes, world.graph).execute(
            ReadNoteRequest(note_id=NID("server-ssh-access")), ctx(RESTRICTED)
        )
        assert high.related == (NID("vault-layout"),)
        assert high.hub == NID("infrastructure")

    def test_hidden_hub_and_superseded_by_are_not_echoed(self, world: World) -> None:
        world.add(id=NID("secret-hub"), type=HUB, priority=Priority.P1, sensitivity=RESTRICTED)
        world.add(id=NID("successor"), sensitivity=RESTRICTED)
        world.add(id=NID("plain"), hub=NID("secret-hub"), related=(NID("missing-note"),))
        world.add(id=NID("plain-old"), status=Status.SUPERSEDED, superseded_by=NID("successor"), priority=Priority.P3)
        read = ReadNote(world.notes, world.graph)
        plain = read.execute(ReadNoteRequest(note_id=NID("plain")), ctx(INTERNAL))
        assert plain.hub is None
        assert plain.related == ()
        old = read.execute(ReadNoteRequest(note_id=NID("plain-old"), include_history=True), ctx(INTERNAL))
        assert old.superseded_by is None

    def test_search_rechecks_hits_from_a_faulty_index(self, world: World) -> None:
        class Leaky:
            def search(self, query: object) -> object:
                return world.search.search(replace(query, ceiling=ctx(RESTRICTED).ceiling, include_history=True))  # type: ignore[type-var,arg-type]

        results = SearchNotes(Leaky()).execute(SearchNotesRequest(), ctx(INTERNAL))  # type: ignore[arg-type]
        assert {r.note.id.value for r in results.results} == {"core-rules", "infrastructure", "server-ssh-access"}

    def test_overview_rechecks_hubs_from_a_faulty_index(self, world: World) -> None:
        world.add(id=NID("secret-hub"), type=HUB, priority=Priority.P1, sensitivity=RESTRICTED)

        class Leaky:
            def search(self, query: object) -> object:
                return world.search.search(replace(query, ceiling=ctx(RESTRICTED).ceiling))  # type: ignore[type-var,arg-type]

        overview = GetOverview(world.notes, Leaky()).execute(GetOverviewRequest(), ctx(INTERNAL))  # type: ignore[arg-type]
        assert [h.id.value for h in overview.hubs] == ["infrastructure"]

    def test_graph_rechecks_nodes_from_a_faulty_index(self, world: World) -> None:
        class Leaky:
            def graph(self, *, ceiling: object, include_history: bool = False) -> object:
                return world.graph.graph(ceiling=ctx(RESTRICTED).ceiling, include_history=True)

            def neighbors(
                self, note_id: object, *, hops: int, ceiling: object, include_history: bool = False
            ) -> object:
                return world.graph.neighbors(note_id, hops=hops, ceiling=ctx(RESTRICTED).ceiling, include_history=True)  # type: ignore[arg-type]

        full = GetGraph(Leaky()).execute(GetGraphRequest(), ctx(INTERNAL))  # type: ignore[arg-type]
        assert {n.id.value for n in full.nodes} == {"core-rules", "infrastructure", "server-ssh-access"}
        near = GetNeighbors(world.notes, Leaky()).execute(  # type: ignore[arg-type]
            GetNeighborsRequest(note_id=NID("infrastructure"), hops=3), ctx(INTERNAL)
        )
        assert "vault-layout" not in {n.id.value for n in near.nodes}
        assert all(e.source.value != "vault-layout" and e.target.value != "vault-layout" for e in near.edges)


def test_an_unservable_note_file_reads_as_missing_below_the_full_ceiling() -> None:
    w = standard_world()
    w.notes.unreadable.add(NID("vault-layout"))  # restricted, edited by hand and not committed
    case = ReadNote(w.notes, w.graph)
    internal = ctx(Sensitivity.INTERNAL)
    with pytest.raises(NoteNotFound) as hidden:
        case.execute(ReadNoteRequest(note_id=NID("vault-layout")), internal)
    with pytest.raises(NoteNotFound) as missing:
        case.execute(ReadNoteRequest(note_id=NID("absent-note")), internal)
    assert str(hidden.value) == str(missing.value)
    view = case.execute(ReadNoteRequest(note_id=NID("server-ssh-access")), internal)  # links to it: redacted
    assert view.related == ()
    with pytest.raises(NoteReadError):  # nothing can be hidden at the full ceiling: the failure is reported
        case.execute(ReadNoteRequest(note_id=NID("vault-layout")), ctx(Sensitivity.RESTRICTED))
