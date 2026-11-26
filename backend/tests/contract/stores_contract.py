"""Contracts for ``ProposalStore``, ``TokenStore``, ``Curator``, ``Cipher``, ``Clock`` and ``IdGenerator``."""

import hashlib
import re
from datetime import UTC, datetime, timedelta

import pytest

from cerebro.application.errors import (
    AlreadyExists,
    DecryptionError,
    TokenNotFound,
)
from cerebro.application.ports import Cipher, Clock, IdGenerator, TokenStore
from cerebro.domain.access import AccessCeiling, Scope
from cerebro.domain.agent_token import AgentToken
from cerebro.domain.enums import Sensitivity
from cerebro.domain.token_hash import TokenHash

T0 = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def make_token(token_id: str = "token-0001", secret: str = "s1", *, at: datetime = T0) -> AgentToken:
    return AgentToken(
        id=token_id,
        agent="claude-code",
        ceiling=AccessCeiling(Sensitivity.INTERNAL),
        scopes=frozenset({Scope.READ}),
        token_hash=TokenHash(hashlib.sha256(secret.encode()).hexdigest()),
        created_at=at,
    )


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
        edits = [ids.new_edit_id() for _ in range(5)]
        tokens = [ids.new_token_id() for _ in range(5)]
        assert len(set(edits)) == 5
        assert len(set(tokens)) == 5
        assert all(pattern.fullmatch(i) for i in (*edits, *tokens))

    def test_secrets_are_unique_and_long_enough(self, ids: IdGenerator) -> None:
        secrets = {ids.new_token_secret() for _ in range(5)}
        assert len(secrets) == 5
        assert all(len(s) >= 32 for s in secrets)
