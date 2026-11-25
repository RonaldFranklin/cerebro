import itertools
from typing import Any, cast

import pytest

from cerebro.domain.access import (
    AccessCeiling,
    AccessContext,
    Agent,
    Owner,
    Scope,
    effective_ceiling,
    is_visible,
)
from cerebro.domain.enums import Sensitivity, Status
from cerebro.domain.errors import DomainError, InvalidValueError

LEVELS = list(Sensitivity)
PUB, INT, RES = Sensitivity.PUBLIC, Sensitivity.INTERNAL, Sensitivity.RESTRICTED
# Written out by hand so the expected minimum does not depend on the ordering code under test.
MIN_TABLE = [
    (PUB, PUB, PUB), (PUB, INT, PUB), (PUB, RES, PUB),
    (INT, PUB, PUB), (INT, INT, INT), (INT, RES, INT),
    (RES, PUB, PUB), (RES, INT, INT), (RES, RES, RES),
]  # fmt: skip


class TestAccessCeiling:
    def test_wraps_a_sensitivity(self) -> None:
        assert AccessCeiling(Sensitivity.INTERNAL).level is Sensitivity.INTERNAL

    @pytest.mark.parametrize("value", ["restricted", "internal", None, 3])
    def test_rejects_anything_but_a_sensitivity(self, value: object) -> None:
        with pytest.raises(InvalidValueError) as excinfo:
            AccessCeiling(cast(Any, value))
        assert excinfo.value.field == "ceiling"

    @pytest.mark.parametrize(
        ("ceiling", "note", "expected"),
        [(c, n, n <= c) for c, n in itertools.product(LEVELS, LEVELS)],
    )
    def test_allows_notes_up_to_and_including_the_ceiling(
        self, ceiling: Sensitivity, note: Sensitivity, expected: bool
    ) -> None:
        assert AccessCeiling(ceiling).allows(note) is expected

    def test_denies_values_that_are_not_sensitivities(self) -> None:
        ceiling = AccessCeiling(Sensitivity.RESTRICTED)
        assert ceiling.allows(cast(Any, "public")) is False
        assert ceiling.allows(cast(Any, None)) is False

    def test_is_hashable_and_comparable(self) -> None:
        assert AccessCeiling(Sensitivity.PUBLIC) == AccessCeiling(Sensitivity.PUBLIC)
        assert len({AccessCeiling(Sensitivity.PUBLIC), AccessCeiling(Sensitivity.PUBLIC)}) == 1


class TestEffectiveCeiling:
    @pytest.mark.parametrize(("listener", "token"), list(itertools.product(LEVELS, LEVELS)))
    def test_is_the_minimum_for_every_pair(self, listener: Sensitivity, token: Sensitivity) -> None:
        result = effective_ceiling(AccessCeiling(listener), AccessCeiling(token))
        assert result.level is min(listener, token)

    @pytest.mark.parametrize(("listener", "token", "expected"), MIN_TABLE)
    def test_matches_the_hand_written_table(
        self, listener: Sensitivity, token: Sensitivity, expected: Sensitivity
    ) -> None:
        assert effective_ceiling(AccessCeiling(listener), AccessCeiling(token)).level is expected
        assert effective_ceiling(AccessCeiling(token), AccessCeiling(listener)).level is expected

    def test_token_can_only_lower_the_listener_ceiling(self) -> None:
        public_listener = AccessCeiling(Sensitivity.INTERNAL)
        powerful_token = AccessCeiling(Sensitivity.RESTRICTED)
        assert effective_ceiling(public_listener, powerful_token) == public_listener

    def test_both_arguments_are_required(self) -> None:
        with pytest.raises(TypeError):
            effective_ceiling(AccessCeiling(Sensitivity.PUBLIC))  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            effective_ceiling()  # type: ignore[call-arg]

    @pytest.mark.parametrize("bad", [None, "restricted", Sensitivity.RESTRICTED])
    def test_rejects_arguments_that_are_not_ceilings(self, bad: object) -> None:
        good = AccessCeiling(Sensitivity.PUBLIC)
        with pytest.raises(InvalidValueError):
            effective_ceiling(cast(Any, bad), good)
        with pytest.raises(InvalidValueError):
            effective_ceiling(good, cast(Any, bad))


class TestVisibility:
    @pytest.mark.parametrize(("ceiling", "note"), list(itertools.product(LEVELS, LEVELS)))
    @pytest.mark.parametrize("status", [s for s in Status if s.is_current])
    def test_current_notes_are_visible_up_to_the_ceiling(
        self, ceiling: Sensitivity, note: Sensitivity, status: Status
    ) -> None:
        assert is_visible(note, status, AccessCeiling(ceiling)) is (note <= ceiling)

    @pytest.mark.parametrize("status", [Status.SUPERSEDED, Status.ARCHIVED])
    def test_history_is_hidden_by_default(self, status: Status) -> None:
        ceiling = AccessCeiling(Sensitivity.RESTRICTED)
        assert is_visible(Sensitivity.PUBLIC, status, ceiling) is False
        assert is_visible(Sensitivity.PUBLIC, status, ceiling, include_history=False) is False

    @pytest.mark.parametrize("status", [Status.SUPERSEDED, Status.ARCHIVED])
    def test_include_history_shows_history_within_the_ceiling(self, status: Status) -> None:
        ceiling = AccessCeiling(Sensitivity.INTERNAL)
        assert is_visible(Sensitivity.INTERNAL, status, ceiling, include_history=True) is True

    @pytest.mark.parametrize("status", list(Status))
    def test_include_history_never_lifts_the_ceiling(self, status: Status) -> None:
        ceiling = AccessCeiling(Sensitivity.INTERNAL)
        assert is_visible(Sensitivity.RESTRICTED, status, ceiling, include_history=True) is False

    def test_include_history_must_be_passed_by_keyword(self) -> None:
        with pytest.raises(TypeError):
            is_visible(  # type: ignore[call-arg]
                Sensitivity.PUBLIC, Status.ARCHIVED, AccessCeiling(Sensitivity.PUBLIC), True
            )

    def test_unknown_values_are_denied(self) -> None:
        ceiling = AccessCeiling(Sensitivity.RESTRICTED)
        assert is_visible(cast(Any, "public"), Status.CURRENT, ceiling) is False
        assert is_visible(Sensitivity.PUBLIC, cast(Any, "current"), ceiling) is False
        assert is_visible(Sensitivity.PUBLIC, Status.CURRENT, cast(Any, None)) is False
        assert is_visible(Sensitivity.PUBLIC, Status.CURRENT, cast(Any, Sensitivity.RESTRICTED)) is False

    def test_a_non_boolean_include_history_does_not_enable_history(self) -> None:
        ceiling = AccessCeiling(Sensitivity.RESTRICTED)
        assert is_visible(Sensitivity.PUBLIC, Status.ARCHIVED, ceiling, include_history=cast(Any, "yes")) is False


class TestPrincipal:
    def test_agent_has_a_validated_name(self) -> None:
        assert Agent("claude-code@workstation").name == "claude-code@workstation"

    @pytest.mark.parametrize(
        "name", ["", " ", "Claude", "a b", "a" * 65, "@host", "x@", "a@b@c", "line\nbreak", "../x", "owner\x00"]
    )
    def test_agent_rejects_invalid_names(self, name: str) -> None:
        with pytest.raises(InvalidValueError) as excinfo:
            Agent(name)
        assert excinfo.value.field == "agent"

    @pytest.mark.parametrize("name", ["owner", "owner@workstation"])
    def test_agent_cannot_use_the_owner_name(self, name: str) -> None:
        with pytest.raises(InvalidValueError) as excinfo:
            Agent(name)
        assert excinfo.value.field == "agent"

    def test_names_merely_containing_owner_are_allowed(self) -> None:
        assert Agent("owner-bot@box").name == "owner-bot@box"
        assert Agent("codex@owner").name == "codex@owner"

    def test_agent_rejects_non_strings(self) -> None:
        with pytest.raises(InvalidValueError):
            Agent(cast(Any, None))

    def test_agent_and_owner_are_distinct(self) -> None:
        assert cast(Any, Agent("owner-bot")) != cast(Any, Owner())
        assert Owner() == Owner()


class TestAccessContext:
    def test_agent_context_can_only_use_its_scopes(self) -> None:
        ctx = AccessContext(
            principal=Agent("codex@box"),
            ceiling=AccessCeiling(Sensitivity.INTERNAL),
            scopes=frozenset({Scope.READ}),
        )
        assert ctx.can(Scope.READ) is True
        assert ctx.can(Scope.PROPOSE) is False
        assert ctx.is_owner is False

    def test_owner_has_every_scope_whatever_is_passed(self) -> None:
        ctx = AccessContext(principal=Owner(), ceiling=AccessCeiling(Sensitivity.PUBLIC), scopes=frozenset())
        assert all(ctx.can(scope) for scope in Scope)
        assert ctx.is_owner is True

    def test_owner_is_still_limited_by_the_listener_ceiling(self) -> None:
        ctx = AccessContext.for_owner(AccessCeiling(Sensitivity.INTERNAL))
        assert ctx.ceiling == AccessCeiling(Sensitivity.INTERNAL)
        assert ctx.scopes == frozenset(Scope)
        assert is_visible(Sensitivity.RESTRICTED, Status.CURRENT, ctx.ceiling) is False

    def test_can_denies_unknown_scopes(self) -> None:
        ctx = AccessContext.for_owner(AccessCeiling(Sensitivity.RESTRICTED))
        assert ctx.can(cast(Any, "read")) is False
        assert ctx.can(cast(Any, "admin")) is False
        agent = AccessContext(
            principal=Agent("a"), ceiling=AccessCeiling(Sensitivity.PUBLIC), scopes=frozenset({Scope.READ})
        )
        assert agent.can(cast(Any, "read")) is False

    def test_rejects_malformed_parts(self) -> None:
        ceiling = AccessCeiling(Sensitivity.PUBLIC)
        with pytest.raises(InvalidValueError):
            AccessContext(principal=cast(Any, "owner"), ceiling=ceiling, scopes=frozenset())
        with pytest.raises(InvalidValueError):
            AccessContext(principal=Owner(), ceiling=cast(Any, Sensitivity.PUBLIC), scopes=frozenset())
        with pytest.raises(InvalidValueError):
            AccessContext(principal=Agent("a"), ceiling=ceiling, scopes=cast(Any, {"read"}))
        with pytest.raises(InvalidValueError):
            AccessContext(principal=Agent("a"), ceiling=ceiling, scopes=cast(Any, [Scope.READ]))

    def test_scope_set_is_frozen_copy(self) -> None:
        ctx = AccessContext(
            principal=Agent("a"), ceiling=AccessCeiling(Sensitivity.PUBLIC), scopes=frozenset({Scope.READ})
        )
        assert isinstance(ctx.scopes, frozenset)

    def test_a_frozenset_subclass_cannot_grant_extra_scopes(self) -> None:
        class Liar(frozenset[Scope]):
            def __contains__(self, item: object) -> bool:
                return True

        ctx = AccessContext(principal=Agent("a"), ceiling=AccessCeiling(Sensitivity.PUBLIC), scopes=Liar({Scope.READ}))
        assert type(ctx.scopes) is frozenset
        assert ctx.can(Scope.PROPOSE) is False


class TestScope:
    def test_minimal_set(self) -> None:
        assert {s.value for s in Scope} == {"read", "propose"}

    def test_parse(self) -> None:
        assert Scope.parse("read") is Scope.READ
        with pytest.raises(InvalidValueError) as excinfo:
            Scope.parse("admin")
        assert excinfo.value.field == "scope"
        assert isinstance(excinfo.value, DomainError)
