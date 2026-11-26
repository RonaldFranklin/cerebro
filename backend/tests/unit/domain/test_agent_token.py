import copy
import pickle
from dataclasses import asdict, astuple, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

import pytest

from cerebro.domain.access import AccessCeiling, Agent, Scope
from cerebro.domain.agent_token import CLOCK_SKEW_TOLERANCE, AgentToken, TokenNotValidError
from cerebro.domain.enums import Sensitivity
from cerebro.domain.errors import DomainError, InvalidValueError
from cerebro.domain.token_hash import TokenHash

T0 = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
HEX = "a" * 64
HASH = TokenHash(HEX)
PUB, INT, RES = Sensitivity.PUBLIC, Sensitivity.INTERNAL, Sensitivity.RESTRICTED


def make_token(**overrides: Any) -> AgentToken:
    fields: dict[str, Any] = {
        "id": "tok-1",
        "agent": "claude-code@workstation",
        "ceiling": AccessCeiling(Sensitivity.INTERNAL),
        "scopes": frozenset({Scope.READ}),
        "token_hash": HASH,
        "created_at": T0,
        "expires_at": None,
        "revoked_at": None,
    }
    fields.update(overrides)
    return AgentToken(**fields)


class TestConstruction:
    def test_valid_token(self) -> None:
        token = make_token()
        assert token.agent == "claude-code@workstation"
        assert token.scopes == frozenset({Scope.READ})

    @pytest.mark.parametrize("bad_id", ["", "  ", "a b", "x" * 65, "line\nbreak"])
    def test_rejects_invalid_ids(self, bad_id: str) -> None:
        with pytest.raises(InvalidValueError) as excinfo:
            make_token(id=bad_id)
        assert excinfo.value.field == "id"

    def test_rejects_invalid_agent_name(self) -> None:
        with pytest.raises(InvalidValueError):
            make_token(agent="Not Valid")

    @pytest.mark.parametrize("bad", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, " " + "a" * 63, "a" * 63 + "\n"])
    def test_hash_must_be_sha256_hex(self, bad: str) -> None:
        with pytest.raises(InvalidValueError) as excinfo:
            TokenHash(bad)
        assert excinfo.value.field == "token_hash"

    def test_token_hash_must_be_a_token_hash(self) -> None:
        with pytest.raises(InvalidValueError) as excinfo:
            make_token(token_hash=HEX)
        assert excinfo.value.field == "token_hash"
        assert HEX not in str(excinfo.value)

    def test_hash_error_does_not_echo_the_value(self) -> None:
        secret = "s3cr3t-plaintext-token-value"
        with pytest.raises(InvalidValueError) as excinfo:
            TokenHash(secret)
        assert secret not in str(excinfo.value)

    def test_scopes_must_be_a_non_empty_frozenset_of_scopes(self) -> None:
        for bad in (frozenset(), {Scope.READ}, [Scope.READ], frozenset({"read"})):
            with pytest.raises(InvalidValueError) as excinfo:
                make_token(scopes=bad)
            assert excinfo.value.field == "scopes"

    def test_core_scope_requires_write(self) -> None:
        for bad in (frozenset({Scope.CORE}), frozenset({Scope.READ, Scope.CORE})):
            with pytest.raises(InvalidValueError) as excinfo:
                make_token(scopes=bad)
            assert excinfo.value.field == "scopes"
        assert make_token(scopes=frozenset({Scope.WRITE, Scope.CORE})).scopes == {Scope.WRITE, Scope.CORE}

    def test_ceiling_must_be_an_access_ceiling(self) -> None:
        with pytest.raises(InvalidValueError):
            make_token(ceiling=Sensitivity.PUBLIC)

    def test_datetimes_must_be_timezone_aware(self) -> None:
        naive = datetime(2026, 11, 25, 12, 0)
        for field in ("created_at", "expires_at", "revoked_at"):
            with pytest.raises(InvalidValueError) as excinfo:
                make_token(**{field: naive})
            assert excinfo.value.field == field

    def test_expiry_must_be_after_creation(self) -> None:
        with pytest.raises(InvalidValueError):
            make_token(expires_at=T0)
        with pytest.raises(InvalidValueError):
            make_token(expires_at=T0 - timedelta(seconds=1))

    def test_revocation_cannot_precede_creation(self) -> None:
        with pytest.raises(InvalidValueError):
            make_token(revoked_at=T0 - timedelta(seconds=1))


class TestValidity:
    def test_valid_without_expiry(self) -> None:
        assert make_token().is_valid(T0 + timedelta(days=3650)) is True

    def test_expires_exactly_at_expires_at(self) -> None:
        token = make_token(expires_at=T0 + timedelta(days=1))
        assert token.is_valid(T0 + timedelta(days=1) - timedelta(microseconds=1)) is True
        assert token.is_valid(T0 + timedelta(days=1)) is False
        assert token.is_valid(T0 + timedelta(days=2)) is False

    def test_comparison_is_timezone_independent(self) -> None:
        token = make_token(expires_at=T0 + timedelta(hours=1))
        later_other_zone = (T0 + timedelta(hours=1)).astimezone(timezone(timedelta(hours=-3)))
        assert token.is_valid(later_other_zone) is False

    def test_revoked_is_invalid_whatever_the_time(self) -> None:
        token = make_token(revoked_at=T0 + timedelta(hours=1))
        assert token.is_valid(T0) is False
        assert token.is_valid(T0 + timedelta(days=1)) is False

    def test_revoke_returns_a_revoked_copy(self) -> None:
        token = make_token()
        revoked = token.revoke(T0 + timedelta(hours=1))
        assert token.revoked_at is None
        assert revoked.revoked_at == T0 + timedelta(hours=1)
        assert revoked.is_valid(T0 + timedelta(hours=2)) is False

    def test_revoke_is_idempotent_and_keeps_the_first_time(self) -> None:
        first = make_token().revoke(T0 + timedelta(hours=1))
        assert first.revoke(T0 + timedelta(hours=5)).revoked_at == T0 + timedelta(hours=1)

    def test_naive_now_is_rejected_not_treated_as_valid(self) -> None:
        with pytest.raises(InvalidValueError) as excinfo:
            make_token().is_valid(datetime(2026, 11, 25, 12, 0))
        assert excinfo.value.field == "now"
        with pytest.raises(InvalidValueError):
            make_token().is_valid(cast(Any, None))

    def test_now_is_required(self) -> None:
        with pytest.raises(TypeError):
            make_token().is_valid()  # type: ignore[call-arg]


class TestNoLeaks:
    HEXES = "deadbeef" * 8

    def test_repr_str_and_format_do_not_contain_the_hash(self) -> None:
        token = make_token(token_hash=TokenHash(self.HEXES))
        for shown in (repr(token), str(token), f"{token}", f"{token.token_hash}", f"{token.token_hash!r}"):
            assert "deadbeef" not in shown
        assert str(token.token_hash) == repr(token.token_hash) == format(token.token_hash, ">20") == "<redacted>"
        assert "token_hash=<redacted>" in repr(token)

    def test_asdict_astuple_and_copies_do_not_expose_the_hash(self) -> None:
        token = make_token(token_hash=TokenHash(self.HEXES))
        assert "deadbeef" not in repr(asdict(token))
        assert "deadbeef" not in repr(astuple(token))
        assert "deadbeef" not in repr(copy.deepcopy(token))
        assert "deadbeef" not in repr(copy.copy(token))
        assert "deadbeef" not in repr(replace(token, id="tok-2"))
        assert copy.deepcopy(token).token_hash.hexdigest == self.HEXES  # still usable by the store

    def test_the_hash_cannot_be_pickled_or_mutated(self) -> None:
        with pytest.raises(TypeError):
            pickle.dumps(TokenHash(self.HEXES))
        with pytest.raises(AttributeError):
            TokenHash(self.HEXES)._hexdigest = "x"
        with pytest.raises(AttributeError):
            del TokenHash(self.HEXES)._hexdigest

    def test_hash_is_available_through_the_explicit_accessor(self) -> None:
        assert make_token(token_hash=TokenHash("b" * 64)).token_hash.hexdigest == "b" * 64

    def test_token_hashes_compare_by_digest_but_tokens_ignore_them(self) -> None:
        assert TokenHash("a" * 64) == TokenHash("a" * 64)
        assert TokenHash("a" * 64) != TokenHash("b" * 64)
        assert TokenHash("a" * 64) != "a" * 64
        assert hash(TokenHash("a" * 64)) == hash(TokenHash("a" * 64))
        one, other = make_token(token_hash=TokenHash("a" * 64)), make_token(token_hash=TokenHash("b" * 64))
        assert one == other  # the owner's rule: the hash is outside __eq__ and __hash__
        assert hash(one) == hash(other)
        assert one.token_hash != other.token_hash
        assert one != make_token(id="tok-2")

    def test_has_no_plaintext_secret_field(self) -> None:
        names = set(AgentToken.__dataclass_fields__)
        assert names == {"id", "agent", "ceiling", "scopes", "token_hash", "created_at", "expires_at", "revoked_at"}

    def test_replace_keeps_validation(self) -> None:
        with pytest.raises(InvalidValueError):
            replace(make_token(), token_hash="nope")  # type: ignore[arg-type]


class TestContextFromToken:
    def test_effective_ceiling_is_min_of_listener_and_token(self) -> None:
        token = make_token(ceiling=AccessCeiling(Sensitivity.RESTRICTED), scopes=frozenset({Scope.READ, Scope.WRITE}))
        ctx = token.to_context(AccessCeiling(Sensitivity.INTERNAL), T0)
        assert ctx.ceiling == AccessCeiling(Sensitivity.INTERNAL)
        assert ctx.principal == Agent("claude-code@workstation")
        assert ctx.token_id == token.id
        assert ctx.scopes == frozenset({Scope.READ, Scope.WRITE})
        assert ctx.can(Scope.WRITE)

    @pytest.mark.parametrize(
        ("listener", "token", "expected"),
        [
            (PUB, PUB, PUB), (PUB, INT, PUB), (PUB, RES, PUB),
            (INT, PUB, PUB), (INT, INT, INT), (INT, RES, INT),
            (RES, PUB, PUB), (RES, INT, INT), (RES, RES, RES),
        ],
    )  # fmt: skip
    def test_context_ceiling_is_min_for_every_pair(
        self, listener: Sensitivity, token: Sensitivity, expected: Sensitivity
    ) -> None:
        ctx = make_token(ceiling=AccessCeiling(token)).to_context(AccessCeiling(listener), T0)
        assert ctx.ceiling.level is expected

    def test_scopes_are_stored_as_a_plain_frozenset(self) -> None:
        class Liar(frozenset[Scope]):
            def __contains__(self, item: object) -> bool:
                return True

        token = make_token(scopes=Liar({Scope.READ}))
        assert type(token.scopes) is frozenset
        assert token.to_context(AccessCeiling(Sensitivity.RESTRICTED), T0).can(Scope.WRITE) is False

    def test_token_lowers_a_permissive_listener(self) -> None:
        token = make_token(ceiling=AccessCeiling(Sensitivity.PUBLIC))
        ctx = token.to_context(AccessCeiling(Sensitivity.RESTRICTED), T0)
        assert ctx.ceiling == AccessCeiling(Sensitivity.PUBLIC)

    @pytest.mark.parametrize(
        "overrides",
        [{"revoked_at": T0}, {"expires_at": T0 + timedelta(seconds=1)}],
    )
    def test_invalid_tokens_never_produce_a_context(self, overrides: dict[str, Any]) -> None:
        token = make_token(**overrides)
        with pytest.raises(TokenNotValidError) as excinfo:
            token.to_context(AccessCeiling(Sensitivity.RESTRICTED), T0 + timedelta(seconds=5))
        assert isinstance(excinfo.value, DomainError)

    def test_listener_is_required(self) -> None:
        with pytest.raises(TypeError):
            make_token().to_context(now=T0)  # type: ignore[call-arg]


class TestCreatedInTheFuture:
    def test_clock_skew_tolerance_is_five_minutes(self) -> None:
        assert timedelta(minutes=5) == CLOCK_SKEW_TOLERANCE

    def test_boundary(self) -> None:
        token = make_token(created_at=T0 + CLOCK_SKEW_TOLERANCE)
        assert token.is_valid(T0) is True  # exactly +5 min ahead of the verifier
        late = make_token(created_at=T0 + CLOCK_SKEW_TOLERANCE + timedelta(seconds=1))
        assert late.is_valid(T0) is False
        assert late.is_valid(T0 + timedelta(seconds=1)) is True

    def test_to_context_refuses_it(self) -> None:
        token = make_token(created_at=T0 + timedelta(hours=1))
        with pytest.raises(TokenNotValidError):
            token.to_context(AccessCeiling(INT), T0)
