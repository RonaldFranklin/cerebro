"""Contracts for ``ProposalStore``, ``TokenStore``, ``Curator``, ``Cipher``, ``Clock`` and ``IdGenerator``."""

import hashlib
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from cerebro.application.errors import (
    AlreadyExists,
    DecryptionError,
    ProposalConflict,
    ProposalLimitReached,
    ProposalNotFound,
    TokenNotFound,
)
from cerebro.application.ports import Cipher, Clock, CurationRequest, Curator, IdGenerator, ProposalStore, TokenStore
from cerebro.domain.access import AccessCeiling, Scope
from cerebro.domain.agent_token import AgentToken
from cerebro.domain.commit_message import CommitMessage
from cerebro.domain.decision import Decision
from cerebro.domain.enums import Sensitivity
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.note import NoteId
from cerebro.domain.proposal import Proposal, ProposalKind, ProposalLimits, ProposalStatus, ProposedContent
from cerebro.domain.report import ValidationReport
from cerebro.domain.token_hash import TokenHash
from tests.fakes.system import FixedClock

T0 = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def make_proposal(proposal_id: str = "proposal-0001", *, agent: str = "claude-code", at: datetime = T0) -> Proposal:
    return Proposal(
        id=proposal_id,
        kind=ProposalKind.CREATE,
        content=ProposedContent(title="A note", body="Body.\n"),
        source_agent=agent,
        created_at=at,
        updated_at=at,
    )


def make_reviewable(proposal_id: str) -> Proposal:
    proposal = make_proposal(proposal_id).mark_validated(ValidationReport(), T0)
    return proposal.mark_curated(
        Decision.needs_owner("review", target=NoteId("review-note")), "curator/null", T0
    ).mark_needs_owner(T0)


def make_token(token_id: str = "token-0001", secret: str = "s1", *, at: datetime = T0) -> AgentToken:
    return AgentToken(
        id=token_id,
        agent="claude-code",
        ceiling=AccessCeiling(Sensitivity.INTERNAL),
        scopes=frozenset({Scope.READ}),
        token_hash=TokenHash(hashlib.sha256(secret.encode()).hexdigest()),
        created_at=at,
    )


class ProposalStoreContract:
    @pytest.fixture
    def store(self) -> ProposalStore:
        raise NotImplementedError

    def test_admissions_use_a_rolling_hour_per_credential_and_count_rejections(self, store: ProposalStore) -> None:
        clock = FixedClock(T0)
        limits = ProposalLimits(per_hour=1, pending=2)
        first = make_reviewable("first").reject("declined", T0)
        store.admit(first, token_id="token-a", limits=limits, clock=clock)
        store.admit(make_reviewable("second"), token_id="token-b", limits=limits, clock=clock)
        clock.advance(timedelta(hours=1, microseconds=-1))
        with pytest.raises(ProposalLimitReached, match=r"^Proposal limit reached\.$"):
            store.admit(make_reviewable("third"), token_id="token-a", limits=limits, clock=clock)
        assert store.get("third") is None
        clock.advance(timedelta(microseconds=1))
        store.admit(make_reviewable("third"), token_id="token-a", limits=limits, clock=clock)

    def test_pending_capacity_reopens_on_terminal_transition_only(self, store: ProposalStore) -> None:
        clock = FixedClock(T0)
        limits = ProposalLimits(per_hour=8, pending=1)
        first = make_reviewable("first")
        store.admit(first, token_id="token", limits=limits, clock=clock)
        approved = store.save(first.approve("owner", T0), expected_revision=0)
        clock.advance(timedelta(days=1))
        with pytest.raises(ProposalLimitReached):
            store.admit(make_reviewable("second"), token_id="token", limits=limits, clock=clock)
        assert store.find() == (approved,)
        store.save(approved.mark_applied(clock.now()), expected_revision=1)
        store.admit(make_reviewable("second"), token_id="token", limits=limits, clock=clock)

    def test_add_then_get(self, store: ProposalStore) -> None:
        proposal = make_proposal()
        store.add(proposal)
        assert store.get("proposal-0001") == proposal
        assert store.get("unknown") is None

    def test_add_duplicate_id_fails(self, store: ProposalStore) -> None:
        store.add(make_proposal())
        with pytest.raises(AlreadyExists):
            store.add(make_proposal())

    def test_save_replaces_and_unknown_fails(self, store: ProposalStore) -> None:
        proposal = make_proposal()
        store.add(proposal)
        moved = proposal.mark_validated(ValidationReport(), T0 + timedelta(minutes=1))
        saved = store.save(moved, expected_revision=0)
        assert saved == replace(moved, revision=1)
        assert store.get(proposal.id) == saved
        with pytest.raises(ProposalNotFound):
            store.save(make_proposal("proposal-0099"), expected_revision=0)

    def test_save_stores_expected_plus_one_whatever_the_proposal_carries(self, store: ProposalStore) -> None:
        proposal = make_proposal()
        store.add(proposal)
        saved = store.save(replace(proposal, revision=41), expected_revision=0)
        assert saved.revision == 1
        stored = store.get(proposal.id)
        assert stored is not None
        assert stored.revision == 1
        assert [p.revision for p in store.find()] == [1]

    def test_unknown_id_is_not_found_even_with_a_nonzero_expected_revision(self, store: ProposalStore) -> None:
        with pytest.raises(ProposalNotFound):
            store.save(make_proposal("proposal-0099"), expected_revision=7)

    def test_add_requires_revision_zero(self, store: ProposalStore) -> None:
        with pytest.raises(InvalidValueError):
            store.add(replace(make_proposal(), revision=3))

    def test_save_with_a_stale_revision_conflicts_and_changes_nothing(self, store: ProposalStore) -> None:
        proposal = make_proposal()
        store.add(proposal)
        first = store.save(proposal.mark_validated(ValidationReport(), T0 + timedelta(minutes=1)), expected_revision=0)
        with pytest.raises(ProposalConflict):
            store.save(proposal.mark_validated(ValidationReport(), T0 + timedelta(minutes=2)), expected_revision=0)
        with pytest.raises(ProposalConflict):
            store.save(first, expected_revision=5)
        assert store.get(proposal.id) == first
        assert first.revision == 1
        assert store.save(first, expected_revision=1).revision == 2

    def test_two_saves_from_the_same_revision_second_fails(self, store: ProposalStore) -> None:
        proposal = make_proposal()
        store.add(proposal)
        loaded_a = store.get(proposal.id)
        loaded_b = store.get(proposal.id)
        assert loaded_a is not None
        assert loaded_b is not None
        store.save(loaded_a.mark_validated(ValidationReport(), T0), expected_revision=loaded_a.revision)
        later = loaded_b.mark_validated(ValidationReport(), T0 + timedelta(minutes=9))
        with pytest.raises(ProposalConflict):
            store.save(later, expected_revision=loaded_b.revision)
        stored = store.get(proposal.id)
        assert stored is not None
        assert stored.updated_at == T0  # the first writer's copy

    def test_find_filters_and_orders_oldest_first(self, store: ProposalStore) -> None:
        store.add(make_proposal("p-late", at=T0 + timedelta(hours=2)))
        store.add(make_proposal("p-b", agent="codex", at=T0))
        store.add(make_proposal("p-a", at=T0))
        assert [p.id for p in store.find()] == ["p-a", "p-b", "p-late"]
        assert [p.id for p in store.find(source_agent="codex")] == ["p-b"]
        assert [p.id for p in store.find(status=ProposalStatus.RECEIVED, source_agent="claude-code")] == [
            "p-a",
            "p-late",
        ]
        assert store.find(status=ProposalStatus.APPLIED) == ()


class TokenStoreContract:
    @pytest.fixture
    def store(self) -> TokenStore:
        raise NotImplementedError

    def test_add_get_and_find_by_hash(self, store: TokenStore) -> None:
        token = make_token()
        store.add(token)
        assert store.get(token.id) == token
        assert store.find_by_hash(token.token_hash) == token
        assert store.get("nope") is None
        assert store.find_by_hash(TokenHash(hashlib.sha256(b"other").hexdigest())) is None

    def test_the_stored_hash_survives_the_roundtrip(self, store: TokenStore) -> None:
        # ``token_hash`` is excluded from ``AgentToken.__eq__``, so compare the digests explicitly.
        token = make_token(secret="s1")
        store.add(token)
        for stored in (store.get(token.id), store.find_by_hash(token.token_hash), *store.all()):
            assert stored is not None
            assert stored.token_hash.hexdigest == hashlib.sha256(b"s1").hexdigest()
        store.save(token.revoke(T0 + timedelta(hours=1)))
        saved = store.get(token.id)
        assert saved is not None
        assert saved.token_hash.hexdigest == token.token_hash.hexdigest

    def test_duplicate_id_or_hash_fails(self, store: TokenStore) -> None:
        store.add(make_token())
        with pytest.raises(AlreadyExists):
            store.add(make_token(secret="different"))
        with pytest.raises(AlreadyExists):
            store.add(make_token("token-0002"))
        assert len(store.all()) == 1

    def test_revoked_token_is_still_found_but_not_valid(self, store: TokenStore) -> None:
        token = make_token()
        store.add(token)
        store.save(token.revoke(T0 + timedelta(hours=1)))
        found = store.find_by_hash(token.token_hash)
        assert found is not None
        assert found.revoked_at == T0 + timedelta(hours=1)
        assert not found.is_valid(T0 + timedelta(hours=2))

    def test_save_unknown_fails(self, store: TokenStore) -> None:
        with pytest.raises(TokenNotFound):
            store.save(make_token())

    def test_all_is_ordered_by_creation_then_id(self, store: TokenStore) -> None:
        store.add(make_token("t-late", "s3", at=T0 + timedelta(days=1)))
        store.add(make_token("t-b", "s2"))
        store.add(make_token("t-a", "s1"))
        assert [t.id for t in store.all()] == ["t-a", "t-b", "t-late"]


class CuratorContract:
    @pytest.fixture
    def curator(self) -> Curator:
        raise NotImplementedError

    def test_has_a_valid_attribution_name(self, curator: Curator) -> None:
        CommitMessage.check_attribution(curator.name, "curated_by")

    def test_returns_a_decision(self, curator: Curator) -> None:
        decision = curator.curate(CurationRequest(proposal=make_proposal()))
        assert isinstance(decision, Decision)

    def test_curation_request_defaults(self) -> None:
        request = CurationRequest(proposal=make_proposal())
        assert request.target is None
        assert request.similar == ()


class CipherContract:
    @pytest.fixture
    def cipher(self) -> Cipher:
        raise NotImplementedError

    def test_roundtrip(self, cipher: Cipher) -> None:
        for payload in (b"", b"secret", "nota com acentuação".encode(), bytes(range(256))):
            assert cipher.decrypt(cipher.encrypt(payload)) == payload

    def test_ciphertext_hides_the_plaintext(self, cipher: Cipher) -> None:
        assert b"secret-password" not in cipher.encrypt(b"secret-password")

    def test_garbage_and_tampering_raise_decryption_error(self, cipher: Cipher) -> None:
        with pytest.raises(DecryptionError):
            cipher.decrypt(b"not a ciphertext")
        token = bytearray(cipher.encrypt(b"payload"))
        token[-3] ^= 0x01
        with pytest.raises(DecryptionError):
            cipher.decrypt(bytes(token))


class ClockContract:
    @pytest.fixture
    def clock(self) -> Clock:
        raise NotImplementedError

    def test_now_is_timezone_aware(self, clock: Clock) -> None:
        now = clock.now()
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)

    def test_time_does_not_go_backwards(self, clock: Clock) -> None:
        assert clock.now() <= clock.now()


class IdGeneratorContract:
    @pytest.fixture
    def ids(self) -> IdGenerator:
        raise NotImplementedError

    def test_ids_are_unique_and_valid(self, ids: IdGenerator) -> None:
        pattern = re.compile(r"[A-Za-z0-9_-]{1,64}")
        proposals = [ids.new_proposal_id() for _ in range(5)]
        tokens = [ids.new_token_id() for _ in range(5)]
        assert len(set(proposals)) == 5
        assert len(set(tokens)) == 5
        assert all(pattern.fullmatch(i) for i in (*proposals, *tokens))

    def test_secrets_are_unique_and_long_enough(self, ids: IdGenerator) -> None:
        secrets = {ids.new_token_secret() for _ in range(5)}
        assert len(secrets) == 5
        assert all(len(s) >= 32 for s in secrets)
