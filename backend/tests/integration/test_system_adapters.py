"""The real system adapters run the shared Clock / IdGenerator contracts plus what the contract cannot see."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from cerebro.adapters.outbound.system import SystemIdGenerator, UtcClock
from cerebro.application.ports import Clock, IdGenerator
from cerebro.domain.proposal import Proposal, ProposalKind, ProposedContent
from tests.contract.stores_contract import ClockContract, IdGeneratorContract

ALPHABET = re.compile(r"[A-Za-z0-9_-]{1,64}")


class TestUtcClock(ClockContract):
    @pytest.fixture
    def clock(self) -> Clock:
        return UtcClock()

    def test_is_close_to_the_system_time(self, clock: Clock) -> None:
        assert abs(datetime.now(UTC) - clock.now()) < timedelta(seconds=5)

    def test_offset_is_utc(self, clock: Clock) -> None:
        assert clock.now().tzinfo is UTC


class TestSystemIdGenerator(IdGeneratorContract):
    @pytest.fixture
    def ids(self) -> IdGenerator:
        return SystemIdGenerator()

    def test_secret_has_at_least_256_bits(self, ids: IdGenerator) -> None:
        # token_urlsafe(32) is 32 bytes = 43 base64url characters.
        assert all(len(ids.new_token_secret()) >= 43 for _ in range(50))

    def test_many_ids_and_secrets_are_unique_and_valid(self, ids: IdGenerator) -> None:
        proposals = [ids.new_proposal_id() for _ in range(5000)]
        tokens = [ids.new_token_id() for _ in range(5000)]
        secrets_ = [ids.new_token_secret() for _ in range(5000)]
        assert len(set(proposals)) == len(set(tokens)) == len(set(secrets_)) == 5000
        assert all(ALPHABET.fullmatch(i) for i in (*proposals, *tokens))
        assert all(re.fullmatch(r"[A-Za-z0-9_-]+", s) for s in secrets_)

    def test_proposal_ids_sort_by_creation_order(self, ids: IdGenerator) -> None:
        # uuid7 is monotonic within the process even inside one millisecond, so no sleeping is needed.
        created = [ids.new_proposal_id() for _ in range(5000)]
        assert created == sorted(created)

    def test_proposal_ids_sort_across_generator_instances_in_one_process(self) -> None:
        # Same process only: the uuid7 state is process-wide. No claim about ordering across processes.
        created = [SystemIdGenerator().new_proposal_id() for _ in range(100)]
        assert created == sorted(created)

    def test_proposal_ids_pass_proposal_validation(self, ids: IdGenerator) -> None:
        now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
        proposal_id = ids.new_proposal_id()
        proposal = Proposal(
            id=proposal_id,
            kind=ProposalKind.CREATE,
            content=ProposedContent(title="A note", body="Body.\n"),
            source_agent="claude-code",
            created_at=now,
            updated_at=now,
        )
        assert proposal.id == proposal_id

    def test_ids_are_unique_across_threads(self, ids: IdGenerator) -> None:
        with ThreadPoolExecutor(max_workers=8) as pool:
            batches = list(pool.map(lambda _: [ids.new_proposal_id() for _ in range(500)], range(8)))
        flat = [i for batch in batches for i in batch]
        assert len(set(flat)) == len(flat) == 4000
        assert all(batch == sorted(batch) for batch in batches)
