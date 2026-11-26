"""Contracts for ``SearchIndex`` and ``GraphIndex``."""

import pytest

from cerebro.application.ports import GraphIndex, SearchIndex, SearchQuery
from cerebro.domain.access import AccessCeiling
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.links import LinkKind
from cerebro.domain.note import Note, NoteId
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.vocabulary import Area, NoteType
from tests.unit.domain.builders import make_note

PUBLIC = AccessCeiling(Sensitivity.PUBLIC)
INTERNAL = AccessCeiling(Sensitivity.INTERNAL)
RESTRICTED = AccessCeiling(Sensitivity.RESTRICTED)


def note(name: str, **overrides: object) -> Note:
    fields: dict[str, object] = {"id": NoteId(name), "hub": None, "related": (), "body": "Nothing special.\n"}
    fields.update(overrides)
    return make_note(**fields)


def ids(hits: tuple[object, ...]) -> list[str]:
    return [h.id.value for h in hits]  # type: ignore[attr-defined]


class SearchIndexContract:
    @pytest.fixture
    def index(self) -> SearchIndex:
        raise NotImplementedError

    def test_query_validates_its_arguments(self) -> None:
        with pytest.raises(InvalidValueError):
            SearchQuery(ceiling=RESTRICTED, limit=0)
        with pytest.raises(InvalidValueError):
            SearchQuery(ceiling=RESTRICTED, limit=101)
        with pytest.raises(InvalidValueError):
            SearchQuery(ceiling="restricted", text="x")  # type: ignore[arg-type]

    def test_finds_by_text_case_insensitively_and_returns_summaries(self, index: SearchIndex) -> None:
        index.upsert(note("docker-notes", title="Docker conventions", summary="How containers are named."))
        index.upsert(note("other-note", title="Unrelated", summary="Something else."))
        (hit,) = index.search(SearchQuery(ceiling=RESTRICTED, text="DOCKER"))
        assert hit.id == NoteId("docker-notes")
        assert hit.title == "Docker conventions"
        assert hit.summary == "How containers are named."
        assert hit.score > 0

    def test_no_match_returns_nothing(self, index: SearchIndex) -> None:
        index.upsert(note("docker-notes", title="Docker conventions"))
        assert index.search(SearchQuery(ceiling=RESTRICTED, text="kubernetes")) == ()

    def test_title_match_outranks_body_only_match(self, index: SearchIndex) -> None:
        index.upsert(note("in-body", title="Plain title", body="We use kubernetes here.\n"))
        index.upsert(note("in-title", title="Kubernetes cluster", body="Plain body.\n"))
        assert ids(index.search(SearchQuery(ceiling=RESTRICTED, text="kubernetes"))) == ["in-title", "in-body"]

    def test_ties_break_by_priority_then_id(self, index: SearchIndex) -> None:
        for name, priority in (("b-note", Priority.P2), ("a-note", Priority.P2), ("c-note", Priority.P1)):
            index.upsert(note(name, title="Same title", priority=priority))
        hits = index.search(SearchQuery(ceiling=RESTRICTED, text="same"))
        assert ids(hits) == ["c-note", "a-note", "b-note"]

    def test_empty_text_lists_by_priority_then_id(self, index: SearchIndex) -> None:
        index.upsert(note("b-note", priority=Priority.P3))
        index.upsert(note("a-note", priority=Priority.P3))
        index.upsert(note("z-note", priority=Priority.P0))
        assert ids(index.search(SearchQuery(ceiling=RESTRICTED))) == ["z-note", "a-note", "b-note"]

    def test_text_without_searchable_words_matches_nothing_and_blank_text_lists(self, index: SearchIndex) -> None:
        index.upsert(note("a-note", title="Plain"))
        assert index.search(SearchQuery(ceiling=RESTRICTED, text="!!! ???")) == ()
        assert index.search(SearchQuery(ceiling=RESTRICTED, text='"')) == ()
        assert ids(index.search(SearchQuery(ceiling=RESTRICTED, text="   "))) == ["a-note"]

    def test_limit_applies_after_ordering(self, index: SearchIndex) -> None:
        for name in ("a-note", "b-note", "c-note"):
            index.upsert(note(name))
        assert ids(index.search(SearchQuery(ceiling=RESTRICTED, limit=2))) == ["a-note", "b-note"]

    def test_filters_combine(self, index: SearchIndex) -> None:
        index.upsert(note("hit-note", area=Area("infrastructure"), type=NoteType("runbook"), priority=Priority.P1))
        index.upsert(note("wrong-area", area=Area("finance"), type=NoteType("runbook"), priority=Priority.P1))
        index.upsert(note("wrong-type", area=Area("infrastructure"), type=NoteType("decision"), priority=Priority.P1))
        index.upsert(
            note("wrong-priority", area=Area("infrastructure"), type=NoteType("runbook"), priority=Priority.P3)
        )
        query = SearchQuery(
            ceiling=RESTRICTED,
            areas=frozenset({Area("infrastructure")}),
            types=frozenset({NoteType("runbook")}),
            priorities=frozenset({Priority.P0, Priority.P1}),
            statuses=frozenset({Status.VERIFIED}),
        )
        assert ids(index.search(query)) == ["hit-note"]

    def test_ceiling_hides_notes_without_trace(self, index: SearchIndex) -> None:
        index.upsert(note("open-note", title="Shared topic", sensitivity=Sensitivity.PUBLIC))
        index.upsert(note("inner-note", title="Shared topic", sensitivity=Sensitivity.INTERNAL))
        index.upsert(note("secret-note", title="Shared topic", sensitivity=Sensitivity.RESTRICTED))
        assert ids(index.search(SearchQuery(ceiling=PUBLIC, text="shared"))) == ["open-note"]
        assert ids(index.search(SearchQuery(ceiling=INTERNAL, text="shared"))) == ["inner-note", "open-note"]
        assert len(index.search(SearchQuery(ceiling=RESTRICTED, text="shared"))) == 3
        assert ids(index.search(SearchQuery(ceiling=PUBLIC))) == ["open-note"]

    def test_history_needs_include_history(self, index: SearchIndex) -> None:
        index.upsert(note("old-note", status=Status.SUPERSEDED))
        index.upsert(note("archived-note", status=Status.ARCHIVED))
        index.upsert(note("live-note"))
        assert ids(index.search(SearchQuery(ceiling=RESTRICTED))) == ["live-note"]
        with_history = index.search(SearchQuery(ceiling=RESTRICTED, include_history=True))
        assert ids(with_history) == ["archived-note", "live-note", "old-note"]

    def test_history_never_raises_the_ceiling(self, index: SearchIndex) -> None:
        index.upsert(note("old-secret", status=Status.SUPERSEDED, sensitivity=Sensitivity.RESTRICTED))
        assert index.search(SearchQuery(ceiling=PUBLIC, include_history=True)) == ()

    def test_upsert_replaces_and_remove_drops(self, index: SearchIndex) -> None:
        index.upsert(note("moving-note", title="Old words"))
        index.upsert(note("moving-note", title="New words"))
        assert index.search(SearchQuery(ceiling=RESTRICTED, text="old")) == ()
        assert ids(index.search(SearchQuery(ceiling=RESTRICTED, text="new"))) == ["moving-note"]
        index.remove(NoteId("moving-note"))
        index.remove(NoteId("never-existed"))
        assert index.search(SearchQuery(ceiling=RESTRICTED)) == ()

    def test_rebuild_replaces_everything(self, index: SearchIndex) -> None:
        index.upsert(note("stale-note"))
        index.rebuild([note("fresh-note")])
        assert ids(index.search(SearchQuery(ceiling=RESTRICTED))) == ["fresh-note"]

    def test_a_new_index_was_built_from_nothing(self, index: SearchIndex) -> None:
        assert index.built_from() is None

    def test_rebuild_records_the_revision_and_set_revision_replaces_it(self, index: SearchIndex) -> None:
        index.rebuild([note("a-note")], revision="rev-1")
        assert index.built_from() == "rev-1"
        index.set_revision("rev-2")
        assert index.built_from() == "rev-2"
        index.rebuild([note("a-note")])
        assert index.built_from() is None

    def test_upsert_and_remove_keep_the_recorded_revision(self, index: SearchIndex) -> None:
        index.rebuild([], revision="rev-1")
        index.upsert(note("a-note"))
        index.remove(NoteId("a-note"))
        assert index.built_from() == "rev-1"

    def test_find_similar_ranks_near_duplicates_first(self, index: SearchIndex) -> None:
        index.upsert(
            note(
                "ssh-access",
                title="SSH access to the home server",
                summary="Reach the server over SSH.",
                body="Use the workstation key to connect over SSH.\n",
            )
        )
        index.upsert(note("recipes", title="Pasta recipes", summary="Dinner ideas.", body="Boil water.\n"))
        index.upsert(
            note("ssh-partial", title="Server inventory", summary="List of machines.", body="Access is via SSH.\n")
        )
        hits = index.find_similar(
            "SSH access to the home server. Use the workstation key to connect over SSH.", ceiling=RESTRICTED
        )
        assert ids(hits)[0] == "ssh-access"
        assert "recipes" not in ids(hits)
        assert all(0 < h.score <= 1 for h in hits)
        assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)

    def test_find_similar_respects_ceiling_history_and_limit(self, index: SearchIndex) -> None:
        index.upsert(note("secret-dup", title="Backup plan", sensitivity=Sensitivity.RESTRICTED))
        index.upsert(note("old-dup", title="Backup plan", status=Status.SUPERSEDED))
        for name in ("dup-a", "dup-b", "dup-c"):
            index.upsert(note(name, title="Backup plan", sensitivity=Sensitivity.INTERNAL))
        assert "secret-dup" not in ids(index.find_similar("Backup plan", ceiling=INTERNAL))
        assert "old-dup" not in ids(index.find_similar("Backup plan", ceiling=RESTRICTED))
        assert len(index.find_similar("Backup plan", ceiling=INTERNAL, limit=2)) == 2

    def test_find_similar_with_nothing_in_common_is_empty(self, index: SearchIndex) -> None:
        index.upsert(note("recipes", title="Pasta recipes", summary="Dinner ideas.", body="Boil water.\n"))
        assert index.find_similar("quantum chromodynamics", ceiling=RESTRICTED) == ()


def ref(name: str, **overrides: object) -> NoteRef:
    return NoteRef.from_note(note(name, **overrides))


def edge_set(edges: tuple[object, ...]) -> set[tuple[str, str, str]]:
    return {(e.source.value, e.target.value, e.kind.value) for e in edges}  # type: ignore[attr-defined]


class GraphIndexContract:
    @pytest.fixture
    def graph(self) -> GraphIndex:
        raise NotImplementedError

    def seed(self, graph: GraphIndex) -> None:
        # hub <- a -> b (body link), b related c, island alone, c -> ghost (broken)
        graph.rebuild(
            [
                ref("hub-note", type=NoteType("hub")),
                ref("a-note", hub=NoteId("hub-note"), body="See [[b-note]].\n"),
                ref("b-note", hub=NoteId("hub-note"), related=(NoteId("c-note"),)),
                ref("c-note", body="Broken: [[ghost-note]].\n"),
                ref("island-note"),
            ]
        )

    def test_graph_has_all_visible_nodes_and_edges(self, graph: GraphIndex) -> None:
        self.seed(graph)
        result = graph.graph(ceiling=RESTRICTED)
        assert [n.id.value for n in result.nodes] == ["a-note", "b-note", "c-note", "hub-note", "island-note"]
        assert edge_set(result.edges) == {
            ("a-note", "hub-note", "hub"),
            ("a-note", "b-note", "body"),
            ("b-note", "hub-note", "hub"),
            ("b-note", "c-note", "related"),
        }
        assert [(e.source.value, e.target.value) for e in result.edges] == sorted(
            (e.source.value, e.target.value) for e in result.edges
        )

    def test_node_area_and_sensitivity_follow_rebuild_and_update(self, graph: GraphIndex) -> None:
        graph.rebuild([ref("a-note", area=Area("work"), sensitivity=Sensitivity.PUBLIC)])
        (node,) = graph.graph(ceiling=PUBLIC).nodes
        assert (node.area, node.sensitivity) == (Area("work"), Sensitivity.PUBLIC)
        graph.upsert(ref("a-note", area=Area("home"), sensitivity=Sensitivity.INTERNAL))
        assert graph.graph(ceiling=PUBLIC).nodes == ()
        (node,) = graph.neighbors(NoteId("a-note"), hops=1, ceiling=INTERNAL).nodes
        assert (node.area, node.sensitivity) == (Area("home"), Sensitivity.INTERNAL)

    def test_backlinks(self, graph: GraphIndex) -> None:
        self.seed(graph)
        assert edge_set(graph.backlinks(NoteId("hub-note"), ceiling=RESTRICTED)) == {
            ("a-note", "hub-note", "hub"),
            ("b-note", "hub-note", "hub"),
        }
        assert graph.backlinks(NoteId("island-note"), ceiling=RESTRICTED) == ()
        assert graph.backlinks(NoteId("nobody"), ceiling=RESTRICTED) == ()

    def test_neighbor_summary_tracks_rebuild_and_update(self, graph: GraphIndex) -> None:
        graph.rebuild([ref("a-note", related=(NoteId("b-note"),)), ref("b-note", summary="First summary.")])

        def summary() -> str:
            return next(
                n.summary
                for n in graph.neighbors(NoteId("a-note"), hops=1, ceiling=RESTRICTED).nodes
                if n.id == NoteId("b-note")
            )

        assert summary() == "First summary."
        graph.upsert(ref("b-note", summary="Updated summary."))
        assert summary() == "Updated summary."

    def test_neighbors_by_hops_in_both_directions(self, graph: GraphIndex) -> None:
        self.seed(graph)
        one = graph.neighbors(NoteId("b-note"), hops=1, ceiling=RESTRICTED)
        assert [n.id.value for n in one.nodes] == ["a-note", "b-note", "c-note", "hub-note"]
        two = graph.neighbors(NoteId("c-note"), hops=1, ceiling=RESTRICTED)
        assert [n.id.value for n in two.nodes] == ["b-note", "c-note"]
        assert edge_set(two.edges) == {("b-note", "c-note", "related")}
        far = graph.neighbors(NoteId("c-note"), hops=3, ceiling=RESTRICTED)
        assert [n.id.value for n in far.nodes] == ["a-note", "b-note", "c-note", "hub-note"]

    def test_neighbors_follows_long_chains_without_a_hop_cap(self, graph: GraphIndex) -> None:
        graph.rebuild([ref(f"n{i:03d}-note", body=f"[[n{i + 1:03d}-note]]\n") for i in range(40)] + [ref("n040-note")])
        reach = graph.neighbors(NoteId("n000-note"), hops=1000, ceiling=RESTRICTED)
        assert len(reach.nodes) == 41
        assert len(graph.neighbors(NoteId("n000-note"), hops=33, ceiling=RESTRICTED).nodes) == 34

    def test_neighbors_of_unknown_note_is_empty_and_hops_are_validated(self, graph: GraphIndex) -> None:
        self.seed(graph)
        assert graph.neighbors(NoteId("nobody"), hops=1, ceiling=RESTRICTED).nodes == ()
        with pytest.raises(InvalidValueError):
            graph.neighbors(NoteId("a-note"), hops=0, ceiling=RESTRICTED)

    def test_orphans_and_broken_links(self, graph: GraphIndex) -> None:
        self.seed(graph)
        assert graph.orphans(ceiling=RESTRICTED) == (NoteId("island-note"),)
        (broken,) = graph.broken_links(ceiling=RESTRICTED)
        assert (broken.source.value, broken.target.value, broken.kind) == ("c-note", "ghost-note", LinkKind.BODY)

    def test_ceiling_hides_nodes_edges_and_paths(self, graph: GraphIndex) -> None:
        graph.rebuild(
            [
                ref("start-note", sensitivity=Sensitivity.PUBLIC, body="[[secret-note]]\n"),
                ref("secret-note", sensitivity=Sensitivity.RESTRICTED, body="[[far-note]]\n"),
                ref("far-note", sensitivity=Sensitivity.PUBLIC),
            ]
        )
        public = graph.graph(ceiling=PUBLIC)
        assert [n.id.value for n in public.nodes] == ["far-note", "start-note"]
        assert public.edges == ()
        reach = graph.neighbors(NoteId("start-note"), hops=3, ceiling=PUBLIC)
        assert [n.id.value for n in reach.nodes] == ["start-note"]
        assert graph.backlinks(NoteId("secret-note"), ceiling=PUBLIC) == ()
        assert graph.neighbors(NoteId("secret-note"), hops=1, ceiling=PUBLIC).nodes == ()
        assert graph.orphans(ceiling=PUBLIC) == (NoteId("far-note"), NoteId("start-note"))
        # A hidden target is not a broken link, and is not revealed either.
        assert graph.broken_links(ceiling=PUBLIC) == ()
        assert len(graph.neighbors(NoteId("start-note"), hops=3, ceiling=RESTRICTED).nodes) == 3

    def test_history_is_hidden_unless_requested(self, graph: GraphIndex) -> None:
        graph.rebuild([ref("live-note", body="[[old-note]]\n"), ref("old-note", status=Status.SUPERSEDED)])
        assert [n.id.value for n in graph.graph(ceiling=RESTRICTED).nodes] == ["live-note"]
        assert graph.broken_links(ceiling=RESTRICTED) == ()
        full = graph.graph(ceiling=RESTRICTED, include_history=True)
        assert [n.id.value for n in full.nodes] == ["live-note", "old-note"]
        assert edge_set(full.edges) == {("live-note", "old-note", "body")}

    def test_a_new_graph_was_built_from_nothing(self, graph: GraphIndex) -> None:
        assert graph.built_from() is None

    def test_rebuild_records_the_revision_and_set_revision_replaces_it(self, graph: GraphIndex) -> None:
        graph.rebuild([ref("a-note")], revision="rev-1")
        assert graph.built_from() == "rev-1"
        graph.set_revision("rev-2")
        assert graph.built_from() == "rev-2"
        graph.rebuild([ref("a-note")])
        assert graph.built_from() is None

    def test_upsert_and_remove_keep_the_recorded_revision(self, graph: GraphIndex) -> None:
        graph.rebuild([], revision="rev-1")
        graph.upsert(ref("a-note"))
        graph.remove(NoteId("a-note"))
        assert graph.built_from() == "rev-1"

    def test_upsert_remove_and_rebuild(self, graph: GraphIndex) -> None:
        graph.upsert(ref("a-note", body="[[b-note]]\n"))
        assert len(graph.broken_links(ceiling=RESTRICTED)) == 1
        graph.upsert(ref("b-note"))
        assert graph.broken_links(ceiling=RESTRICTED) == ()
        graph.remove(NoteId("b-note"))
        graph.remove(NoteId("never-existed"))
        assert len(graph.broken_links(ceiling=RESTRICTED)) == 1
        graph.rebuild([ref("only-note")])
        assert [n.id.value for n in graph.graph(ceiling=RESTRICTED).nodes] == ["only-note"]
