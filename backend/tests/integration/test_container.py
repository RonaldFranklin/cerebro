"""The composition root: wiring, the writer lock and the start-up reindex (Phase 2 PR 8)."""

import shutil
import stat
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from cerebro.adapters.outbound.filesystem import WriterBusyError
from cerebro.application.dto import (
    ApplyOutcome,
    ProposalIdRequest,
    RebuildIndexRequest,
    ReviewAction,
    ReviewProposalRequest,
    SubmitProposalRequest,
)
from cerebro.application.index_state import index_is_stale
from cerebro.application.ports import NoteChange, SearchQuery
from cerebro.bootstrap.container import Container, Serialized, StateDirectoryError, WriterLock, build_container
from cerebro.domain.access import AccessCeiling, AccessContext, Agent, Scope
from cerebro.domain.commit_message import CommitMessage
from cerebro.domain.enums import Priority, Sensitivity
from cerebro.domain.note import Note, NoteId
from cerebro.domain.proposal import ProposalKind, ProposedContent
from cerebro.domain.vocabulary import Area, NoteType
from tests.integration.content_kit import (
    HUB_ID,
    SampleRepo,
    make_sample_repo,
)
from tests.unit.domain.builders import VALID_SUMMARY, make_note

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the git executable is not installed")

OWNER = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))
AGENT = AccessContext(
    principal=Agent("claude-code"),
    ceiling=AccessCeiling(Sensitivity.INTERNAL),
    scopes=frozenset({Scope.READ, Scope.PROPOSE}),
)


@pytest.fixture
def sample(tmp_path: Path) -> SampleRepo:
    return make_sample_repo(tmp_path)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class TestWiring:
    def test_state_dir_is_created_0700_with_private_files(self, sample: SampleRepo) -> None:
        container = build_container(sample.settings)
        container.start()
        assert mode(sample.state_dir) == 0o700
        assert mode(sample.state_dir / "index.sqlite") == 0o600
        assert mode(sample.state_dir / "state.sqlite") == 0o600

    def test_use_cases_are_exposed_as_attributes(self, sample: SampleRepo) -> None:
        use_cases = build_container(sample.settings).use_cases
        for name in [
            "get_overview",
            "search_notes",
            "read_note",
            "get_neighbors",
            "get_graph",
            "submit_proposal",
            "validate_proposal",
            "curate_proposal",
            "review_proposal",
            "apply_proposal",
            "owner_edit_note",
            "rebuild_index",
            "health_report",
            "issue_agent_token",
            "revoke_agent_token",
            "authenticate_token",
        ]:
            assert hasattr(getattr(use_cases, name), "execute")


class TestStateDirectory:
    def test_an_existing_directory_open_to_others_is_refused_and_not_chmoded(self, sample: SampleRepo) -> None:
        sample.state_dir.mkdir()
        sample.state_dir.chmod(0o755)
        with pytest.raises(StateDirectoryError):
            build_container(sample.settings)
        assert mode(sample.state_dir) == 0o755

    def test_the_remote_is_not_shown_in_the_settings_repr(self, sample: SampleRepo) -> None:
        settings = replace(sample.settings, git_remote="https://user:pw@example.invalid/x.git")
        assert "pw@" not in repr(settings)


class TestStartupReindex:
    def test_a_fresh_container_on_an_unindexed_repository_reindexes(self, sample: SampleRepo) -> None:
        container = build_container(sample.settings)
        assert index_is_stale(container.notes, container.search, container.graph)
        assert container.start() is True
        assert not index_is_stale(container.notes, container.search, container.graph)
        assert container.search.built_from() == container.notes.head()

    def test_a_second_start_with_nothing_changed_does_nothing(self, sample: SampleRepo) -> None:
        build_container(sample.settings).start()
        again = build_container(sample.settings)
        assert again.start() is False

    def test_an_out_of_band_commit_is_picked_up_at_the_next_start(self, sample: SampleRepo) -> None:
        build_container(sample.settings).start()
        extra = make_note(
            id=NoteId("late-addition"),
            title="Late addition",
            type=NoteType("concept"),
            sensitivity=Sensitivity.PUBLIC,
            hub=HUB_ID,
            related=(),
            body="Added by hand with [[docker-conventions]].\n",
        )
        sample.commit_out_of_band(extra)
        restarted = build_container(sample.settings)
        assert index_is_stale(restarted.notes, restarted.search, restarted.graph)
        assert restarted.start() is True
        assert not index_is_stale(restarted.notes, restarted.search, restarted.graph)
        hits = restarted.search.search(SearchQuery(ceiling=AccessCeiling(Sensitivity.PUBLIC), text="Late addition"))
        assert [h.id for h in hits] == [NoteId("late-addition")]

    def test_a_recreated_index_after_corruption_is_rebuilt(self, sample: SampleRepo) -> None:
        first = build_container(sample.settings)
        first.start()
        filler = [
            make_note(id=NoteId(f"filler-{i:03d}"), sensitivity=Sensitivity.PUBLIC, body="word " * 200)
            for i in range(30)
        ]
        first.search.rebuild(filler, revision=first.notes.head())
        index_file = sample.state_dir / "index.sqlite"
        with index_file.open("r+b") as handle:  # scribble over the data pages, keeping the header
            handle.seek(4096)
            handle.write(b"\xde\xad\xbe\xef" * ((index_file.stat().st_size - 4096) // 4))
        recreated = build_container(sample.settings)
        assert recreated.search.built_from() is None
        assert recreated.graph.built_from() is None
        assert recreated.start() is True
        assert recreated.search.built_from() == recreated.notes.head() is not None
        assert recreated.graph.built_from() == recreated.notes.head()

    def test_a_repository_without_notes_is_indexed_once(self, tmp_path: Path) -> None:
        settings = make_sample_repo(tmp_path, notes=[]).settings
        assert build_container(settings).start() is True
        assert build_container(settings).start() is False


class TestWriterLock:
    def test_serialized_use_cases_never_overlap(self, tmp_path: Path) -> None:
        lock = WriterLock(tmp_path / "w.lock", timeout=10)
        active = 0
        peak = 0
        guard = threading.Lock()

        class Probe:
            def execute(self, request: int, ctx: AccessContext) -> int:
                nonlocal active, peak
                with guard:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.02)
                with guard:
                    active -= 1
                return request

        serialized = Serialized(Probe(), lock)
        threads = [threading.Thread(target=serialized.execute, args=(i, OWNER)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert peak == 1

    def test_a_busy_thread_lock_times_out_with_the_busy_error(self, tmp_path: Path) -> None:
        lock = WriterLock(tmp_path / "w.lock", timeout=0.1)
        with lock.held(), pytest.raises(WriterBusyError), lock.held():
            pass

    def test_a_failed_index_update_is_never_hidden_by_a_concurrent_apply(self, sample: SampleRepo) -> None:
        container = build_container(sample.settings)
        container.start()
        real_upsert = container.search.upsert
        real_apply = container.notes.apply
        failing_id = NoteId("note-that-fails")

        def slow_apply(changes: Sequence[NoteChange], message: CommitMessage) -> str:
            # Unserialised, B reads the old head while A is still before its commit, then commits after A.
            time.sleep(0.2 if changes[0].note.id == failing_id else 0.4)
            return real_apply(changes, message)

        def flaky_upsert(note: Note) -> None:
            if note.id == failing_id:
                raise RuntimeError("index unavailable")
            real_upsert(note)

        container.notes.apply = slow_apply  # type: ignore[method-assign]
        container.search.upsert = flaky_upsert  # type: ignore[method-assign]
        failing = approved(container, "Note that fails")
        healthy = approved(container, "Healthy note")
        errors: list[BaseException] = []
        unexpected: list[BaseException] = []

        def run(proposal_id: str) -> None:
            try:
                result = container.use_cases.apply_proposal.execute(ProposalIdRequest(proposal_id=proposal_id), OWNER)
                assert result.outcome is ApplyOutcome.APPLIED
            except RuntimeError as error:
                errors.append(error)
            except BaseException as error:
                unexpected.append(error)

        threads = [threading.Thread(target=run, args=(failing,)), threading.Thread(target=run, args=(healthy,))]
        threads[0].start()
        time.sleep(0.05)
        threads[1].start()
        for thread in threads:
            thread.join()
        assert unexpected == []
        assert len(errors) == 1
        assert index_is_stale(container.notes, container.search, container.graph)
        container.search.upsert = real_upsert  # type: ignore[method-assign]
        container.use_cases.rebuild_index.execute(RebuildIndexRequest(), OWNER)
        assert not index_is_stale(container.notes, container.search, container.graph)


def approved(container: Container, title: str) -> str:
    content = ProposedContent(
        title=title,
        body=f"# {title}\n\nContent of {title}.\n",
        area=Area("infrastructure"),
        type=NoteType("guide"),
        priority=Priority.P2,
        sensitivity=Sensitivity.PUBLIC,
        summary=VALID_SUMMARY,
        hub=HUB_ID,
        rationale="needed",
    )
    cases = container.use_cases
    outcome = cases.submit_proposal.execute(SubmitProposalRequest(kind=ProposalKind.CREATE, content=content), AGENT)
    pid = outcome.proposal_id
    cases.validate_proposal.execute(ProposalIdRequest(proposal_id=pid), OWNER)
    cases.curate_proposal.execute(ProposalIdRequest(proposal_id=pid), OWNER)
    cases.review_proposal.execute(ReviewProposalRequest(proposal_id=pid, action=ReviewAction.APPROVE), OWNER)
    return pid
