"""Phase 2 acceptance: a real content repository (with an encrypted restricted note) is indexed through the
container, queried per ceiling. Real git, files, SQLite, Fernet."""

import shutil
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from cerebro.application.dto import (
    AuthenticateTokenRequest,
    GetGraphRequest,
    GetNeighborsRequest,
    GetOverviewRequest,
    HealthCode,
    HealthReportRequest,
    IssueAgentTokenRequest,
    ReadNoteRequest,
    RebuildIndexRequest,
    SearchNotesRequest,
)
from cerebro.application.errors import AuthenticationFailed, NoteNotFound
from cerebro.application.index_state import index_is_stale
from cerebro.application.ports import SearchQuery
from cerebro.bootstrap.container import Container, build_container
from cerebro.domain.access import AccessCeiling, AccessContext, Scope
from cerebro.domain.enums import Sensitivity
from cerebro.domain.links import LinkKind
from cerebro.domain.note import NoteId
from cerebro.domain.report import Severity, ViolationCode
from cerebro.domain.vocabulary import NoteType
from tests.integration.content_kit import (
    BROKEN_ID,
    CORE_ID,
    DOCKER_ID,
    GHOST_ID,
    HUB_ID,
    ISLAND_ID,
    OLD_ID,
    RESTRICTED_MARKER,
    SSH_ID,
    VAULT_ID,
    SampleRepo,
    make_sample_repo,
)
from tests.integration.test_git import raw_git

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the git executable is not installed")

PUBLIC = AccessCeiling(Sensitivity.PUBLIC)
INTERNAL = AccessCeiling(Sensitivity.INTERNAL)
RESTRICTED = AccessCeiling(Sensitivity.RESTRICTED)
OWNER = AccessContext.for_owner(RESTRICTED)


@pytest.fixture
def sample(tmp_path: Path) -> SampleRepo:
    return make_sample_repo(tmp_path)


@pytest.fixture
def container(sample: SampleRepo) -> Container:
    built = build_container(sample.settings)
    built.use_cases.rebuild_index.execute(RebuildIndexRequest(), OWNER)
    return built


Login = Callable[[AccessCeiling, AccessCeiling], AccessContext]


@pytest.fixture
def login(container: Container) -> Login:
    """The real flow: the owner issues a token, an inbound adapter authenticates it on its listener."""

    def run(token_ceiling: AccessCeiling, listener: AccessCeiling) -> AccessContext:
        issued = container.use_cases.issue_agent_token.execute(
            IssueAgentTokenRequest(agent="claude-code", ceiling=token_ceiling, scopes=frozenset({Scope.READ})), OWNER
        )
        return container.use_cases.authenticate_token.execute(
            AuthenticateTokenRequest(secret=issued.secret, listener=listener)
        )

    return run


def search_ids(container: Container, ctx: AccessContext, text: str = "", **filters: object) -> set[str]:
    results = container.use_cases.search_notes.execute(SearchNotesRequest(text=text, limit=100, **filters), ctx)  # type: ignore[arg-type]
    return {r.note.id.value for r in results.results}


class TestIndexedRepository:
    def test_the_rebuild_indexed_every_note_and_the_indexes_are_fresh(self, container: Container) -> None:
        assert container.search.built_from() == container.notes.head() is not None
        assert container.graph.built_from() == container.notes.head()
        assert not index_is_stale(container.notes, container.search, container.graph)
        assert len(container.notes.list_notes()) == 8

    def test_search_results_follow_the_ceiling_of_the_token_and_the_listener(
        self, container: Container, login: Login
    ) -> None:
        public = login(RESTRICTED, PUBLIC)  # a token may not widen its listener
        internal = login(RESTRICTED, INTERNAL)
        restricted = login(RESTRICTED, RESTRICTED)
        assert public.ceiling == PUBLIC
        assert internal.ceiling == INTERNAL
        assert restricted.ceiling == RESTRICTED
        assert login(PUBLIC, RESTRICTED).ceiling == PUBLIC  # nor may a listener widen its token

        everything = search_ids(container, restricted)
        assert everything == {
            CORE_ID.value,
            HUB_ID.value,
            DOCKER_ID.value,
            SSH_ID.value,
            VAULT_ID.value,
            "broken-link-note",
            ISLAND_ID.value,
        }
        assert search_ids(container, internal) == everything - {VAULT_ID.value}
        assert search_ids(container, public) == everything - {VAULT_ID.value, SSH_ID.value}
        assert search_ids(container, restricted, include_history=True) >= everything | {OLD_ID.value}

    def test_the_restricted_note_is_found_by_its_text_only_at_the_restricted_ceiling(
        self, container: Container, login: Login
    ) -> None:
        assert search_ids(container, login(RESTRICTED, RESTRICTED), RESTRICTED_MARKER) == {VAULT_ID.value}
        assert search_ids(container, login(RESTRICTED, INTERNAL), RESTRICTED_MARKER) == set()
        assert search_ids(container, login(INTERNAL, RESTRICTED), RESTRICTED_MARKER) == set()
        assert search_ids(container, login(PUBLIC, PUBLIC), RESTRICTED_MARKER) == set()
        assert container.search.search(SearchQuery(ceiling=INTERNAL, text=RESTRICTED_MARKER)) == ()
        assert [h.id for h in container.search.search(SearchQuery(ceiling=RESTRICTED, text=RESTRICTED_MARKER))] == [
            VAULT_ID
        ]

    def test_the_restricted_note_is_invisible_in_every_graph_query_below_its_ceiling(
        self, container: Container, login: Login
    ) -> None:
        graph = container.graph
        for ceiling in (PUBLIC, INTERNAL):
            nodes = {n.id for n in graph.graph(ceiling=ceiling).nodes}
            assert VAULT_ID not in nodes
            assert all(VAULT_ID not in (e.source, e.target) for e in graph.graph(ceiling=ceiling).edges)
            assert all(link.source != VAULT_ID for link in graph.backlinks(SSH_ID, ceiling=ceiling))
            assert VAULT_ID not in {n.id for n in graph.neighbors(SSH_ID, hops=3, ceiling=ceiling).nodes}
            assert graph.backlinks(VAULT_ID, ceiling=ceiling) == ()
            assert graph.neighbors(VAULT_ID, hops=1, ceiling=ceiling).nodes == ()
            assert all(link.source != VAULT_ID for link in graph.broken_links(ceiling=ceiling))
            assert VAULT_ID not in graph.orphans(ceiling=ceiling)
        full = {n.id for n in graph.graph(ceiling=RESTRICTED).nodes}
        assert VAULT_ID in full
        assert [
            link.source for link in graph.backlinks(SSH_ID, ceiling=RESTRICTED) if link.kind is LinkKind.RELATED
        ] == [VAULT_ID]
        # The same through the use cases, with real tokens.
        internal = login(RESTRICTED, INTERNAL)
        view = container.use_cases.get_graph.execute(GetGraphRequest(), internal)
        assert VAULT_ID not in {n.id for n in view.nodes}
        near = container.use_cases.get_neighbors.execute(GetNeighborsRequest(note_id=SSH_ID, hops=3), internal)
        assert VAULT_ID not in {n.id for n in near.nodes}
        with pytest.raises(NoteNotFound):
            container.use_cases.read_note.execute(ReadNoteRequest(note_id=VAULT_ID), internal)
        with pytest.raises(NoteNotFound):
            container.use_cases.get_neighbors.execute(GetNeighborsRequest(note_id=VAULT_ID), internal)
        restricted = login(RESTRICTED, RESTRICTED)
        note = container.use_cases.read_note.execute(ReadNoteRequest(note_id=VAULT_ID), restricted)
        assert RESTRICTED_MARKER in note.body

    def test_hub_members_orphans_and_broken_links(self, container: Container, login: Login) -> None:
        def members(ctx: AccessContext) -> set[str]:
            hub = container.use_cases.read_note.execute(ReadNoteRequest(note_id=HUB_ID), ctx)
            assert hub.members is not None
            return {m.id.value for m in hub.members}

        everything = {DOCKER_ID.value, SSH_ID.value, VAULT_ID.value, BROKEN_ID.value}
        assert members(login(RESTRICTED, RESTRICTED)) == everything
        assert members(login(RESTRICTED, INTERNAL)) == everything - {VAULT_ID.value}
        assert members(login(RESTRICTED, PUBLIC)) == everything - {VAULT_ID.value, SSH_ID.value}
        assert container.graph.orphans(ceiling=RESTRICTED) == (ISLAND_ID,)
        (broken,) = container.graph.broken_links(ceiling=RESTRICTED)
        assert (broken.source, broken.target, broken.kind) == (BROKEN_ID, GHOST_ID, LinkKind.BODY)
        overview = container.use_cases.get_overview.execute(GetOverviewRequest(), login(RESTRICTED, PUBLIC))
        assert [c.id for c in overview.core] == [CORE_ID]
        assert {h.id for h in overview.hubs} == {HUB_ID, ISLAND_ID}

    def test_the_health_report_has_exactly_the_planted_findings(self, container: Container) -> None:
        report = container.use_cases.health_report.execute(HealthReportRequest(), OWNER)
        found = {(i.subject, i.code, i.severity) for i in report.issues}
        assert found == {
            (ISLAND_ID.value, HealthCode.ORPHAN_NOTE, Severity.WARNING),
            (BROKEN_ID.value, ViolationCode.BROKEN_LINK, Severity.WARNING),
        }
        assert report.notes_checked == 8
        assert report.search_index_checked

    def test_a_stale_index_is_reported_and_a_rebuild_clears_it(self, container: Container, sample: SampleRepo) -> None:
        from tests.unit.domain.builders import make_note

        sample.commit_out_of_band(
            make_note(
                id=NoteId("late-addition"),
                type=NoteType("concept"),
                sensitivity=Sensitivity.PUBLIC,
                hub=HUB_ID,
                related=(),
                body="Late.\n",
            )
        )
        stale = container.use_cases.health_report.execute(HealthReportRequest(), OWNER)
        assert any(i.severity is Severity.ERROR for i in stale.issues)
        container.use_cases.rebuild_index.execute(RebuildIndexRequest(), OWNER)
        fresh = container.use_cases.health_report.execute(HealthReportRequest(), OWNER)
        assert not any(i.severity is Severity.ERROR for i in fresh.issues)


class TestAtRest:
    def test_the_restricted_note_file_holds_only_the_adr_0008_header_and_ciphertext(
        self, container: Container, sample: SampleRepo
    ) -> None:
        (path,) = list(sample.root.rglob("vault-layout.md"))
        data = path.read_bytes()
        header, token = data.split(b"---\n", 2)[1:]
        assert header == b"id: vault-layout\nsensitivity: restricted\nencryption: fernet-v1\n"
        assert token.endswith(b"\n")
        assert token.count(b"\n") == 1
        assert RESTRICTED_MARKER.encode() not in data
        assert b"Vault layout" not in data
        assert b"unseal" not in data
        committed = raw_git(sample.root, "show", f"HEAD:{path.relative_to(sample.root).as_posix()}")
        assert RESTRICTED_MARKER not in committed

    def test_private_files_have_private_modes_and_head_is_the_recorded_revision(
        self, container: Container, sample: SampleRepo
    ) -> None:
        assert stat.S_IMODE((sample.state_dir / "index.sqlite").stat().st_mode) == 0o600
        assert stat.S_IMODE((sample.state_dir / "state.sqlite").stat().st_mode) == 0o600
        assert stat.S_IMODE(sample.state_dir.stat().st_mode) == 0o700
        head = container.notes.head()
        assert head == raw_git(sample.root, "rev-parse", "HEAD").strip()
        assert container.search.built_from() == head
        assert container.graph.built_from() == head


class TestAuthentication:
    def test_a_token_on_an_internal_listener_never_sees_restricted_notes(
        self, container: Container, login: Login
    ) -> None:
        ctx = login(RESTRICTED, INTERNAL)
        assert ctx.ceiling == INTERNAL
        assert VAULT_ID.value not in search_ids(container, ctx)
        assert VAULT_ID.value not in search_ids(container, ctx, RESTRICTED_MARKER)

    def test_a_wrong_secret_is_refused(self, container: Container) -> None:
        with pytest.raises(AuthenticationFailed):
            container.use_cases.authenticate_token.execute(AuthenticateTokenRequest(secret="x" * 43, listener=INTERNAL))
