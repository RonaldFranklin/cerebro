"""Runs every port contract against the in-memory fakes. Phase 2 adapters get the same suites."""

from datetime import timedelta

import pytest

from cerebro.application.ports import (
    Cipher,
    Clock,
    Curator,
    GraphIndex,
    IdGenerator,
    NoteRepository,
    ProposalStore,
    SearchIndex,
    TokenStore,
    VersionControl,
)
from tests.contract.notes_contract import NoteRepositoryContract
from tests.contract.search_contract import GraphIndexContract, SearchIndexContract
from tests.contract.stores_contract import (
    CipherContract,
    ClockContract,
    CuratorContract,
    IdGeneratorContract,
    ProposalStoreContract,
    TokenStoreContract,
)
from tests.fakes.notes import InMemoryNoteRepository
from tests.fakes.search import InMemoryGraphIndex, InMemorySearchIndex
from tests.fakes.stores import FakeCurator, InMemoryProposalStore, InMemoryTokenStore
from tests.fakes.system import FixedClock, RecordingVersionControl, ReversingCipher, SequentialIdGenerator


class TestInMemoryNoteRepository(NoteRepositoryContract):
    @pytest.fixture
    def repo(self) -> NoteRepository:
        return InMemoryNoteRepository()


class TestInMemorySearchIndex(SearchIndexContract):
    @pytest.fixture
    def index(self) -> SearchIndex:
        return InMemorySearchIndex()


class TestInMemoryGraphIndex(GraphIndexContract):
    @pytest.fixture
    def graph(self) -> GraphIndex:
        return InMemoryGraphIndex()


class TestInMemoryProposalStore(ProposalStoreContract):
    @pytest.fixture
    def store(self) -> ProposalStore:
        return InMemoryProposalStore()


class TestInMemoryTokenStore(TokenStoreContract):
    @pytest.fixture
    def store(self) -> TokenStore:
        return InMemoryTokenStore()


class TestFakeCurator(CuratorContract):
    @pytest.fixture
    def curator(self) -> Curator:
        return FakeCurator()


class TestReversingCipher(CipherContract):
    @pytest.fixture
    def cipher(self) -> Cipher:
        return ReversingCipher()


class TestFixedClock(ClockContract):
    @pytest.fixture
    def clock(self) -> Clock:
        return FixedClock()


class TestSequentialIdGenerator(IdGeneratorContract):
    @pytest.fixture
    def ids(self) -> IdGenerator:
        return SequentialIdGenerator()


def test_fixed_clock_moves_only_when_told() -> None:
    clock = FixedClock()
    start = clock.now()
    clock.advance(timedelta(hours=1))
    assert clock.now() == start + timedelta(hours=1)
    clock.set(start)
    assert clock.now() == start


def test_sequential_ids_are_deterministic() -> None:
    first, second = SequentialIdGenerator(), SequentialIdGenerator()
    assert first.new_proposal_id() == second.new_proposal_id() == "proposal-0001"
    assert first.new_token_secret() == second.new_token_secret()


def test_fake_curator_records_requests_and_uses_the_scripted_decision() -> None:
    from cerebro.application.ports import CurationRequest
    from cerebro.domain.decision import Decision, DecisionKind
    from cerebro.domain.note import NoteId
    from tests.contract.stores_contract import make_proposal

    curator = FakeCurator(Decision.create(NoteId("new-note")), name="scripted")
    request = CurationRequest(proposal=make_proposal())
    assert curator.curate(request).kind is DecisionKind.CREATE
    assert curator.name == "scripted"
    assert curator.requests == [request]
    assert FakeCurator().curate(request).kind is DecisionKind.NEEDS_OWNER


def test_recording_version_control_counts_pushes() -> None:
    vcs: VersionControl = RecordingVersionControl()
    vcs.push()
    assert isinstance(vcs, RecordingVersionControl)
    assert vcs.pushes == 1


def test_in_memory_repository_records_commits() -> None:
    from cerebro.application.ports import NoteChange
    from tests.contract.notes_contract import MESSAGE
    from tests.unit.domain.builders import make_note

    repo = InMemoryNoteRepository()
    revision = repo.apply([NoteChange(note=make_note())], MESSAGE)
    assert repo.commits == [(revision, MESSAGE, (make_note().id,))]
