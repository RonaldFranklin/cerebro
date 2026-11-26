"""Runs every port contract against the in-memory fakes. Phase 2 adapters get the same suites."""

from collections.abc import Callable

import pytest

from cerebro.adapters.outbound.filesystem import NoteWriteError
from cerebro.application.ports import (
    Cipher,
    Clock,
    GraphIndex,
    IdGenerator,
    SearchIndex,
    TokenStore,
)
from cerebro.domain.note import NoteId
from tests.contract.notes_contract import NoteRepositoryContract
from tests.contract.search_contract import GraphIndexContract, SearchIndexContract
from tests.contract.stores_contract import (
    CipherContract,
    ClockContract,
    IdGeneratorContract,
    TokenStoreContract,
)
from tests.fakes.notes import InMemoryNoteRepository
from tests.fakes.search import InMemoryGraphIndex, InMemorySearchIndex
from tests.fakes.stores import InMemoryTokenStore
from tests.fakes.system import FixedClock, ReversingCipher, SequentialIdGenerator


class TestInMemoryNoteRepository(NoteRepositoryContract):
    @pytest.fixture
    def repo(self) -> InMemoryNoteRepository:
        return InMemoryNoteRepository()

    @pytest.fixture
    def break_writes(self, repo: InMemoryNoteRepository) -> Callable[[], None]:
        def fail() -> None:
            repo.apply_error = NoteWriteError("synthetic write failure")

        return fail

    @pytest.fixture
    def edit_uncommitted(self, repo: InMemoryNoteRepository) -> Callable[[NoteId], None]:
        return repo.unreadable.add


class TestInMemorySearchIndex(SearchIndexContract):
    @pytest.fixture
    def index(self) -> SearchIndex:
        return InMemorySearchIndex()


class TestInMemoryGraphIndex(GraphIndexContract):
    @pytest.fixture
    def graph(self) -> GraphIndex:
        return InMemoryGraphIndex()


class TestInMemoryTokenStore(TokenStoreContract):
    @pytest.fixture
    def store(self) -> TokenStore:
        return InMemoryTokenStore()


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
