"""Counting authenticated requests and reporting them to the owner."""

from datetime import timedelta

import pytest

from cerebro.application.dto import IssueAgentTokenRequest, RevokeAgentTokenRequest, TokenActivityRequest
from cerebro.application.errors import PermissionDenied
from cerebro.application.use_cases import IssueAgentToken, RecordTokenUse, RevokeAgentToken, TokenActivity
from cerebro.domain.access import AccessCeiling, AccessContext, Agent, Scope
from cerebro.domain.enums import Sensitivity
from tests.fakes.stores import InMemoryTokenStore
from tests.fakes.system import FixedClock, SequentialIdGenerator
from tests.fakes.usage import FakeUsageCounters
from tests.unit.application.world import ctx

OWNER = ctx(Sensitivity.RESTRICTED, owner=True)
INTERNAL = AccessCeiling(Sensitivity.INTERNAL)


class Kit:
    def __init__(self) -> None:
        self.tokens = InMemoryTokenStore()
        self.clock = FixedClock()
        self.ids = SequentialIdGenerator()
        self.counters = FakeUsageCounters()
        self.record = RecordTokenUse(self.counters, self.clock)
        self.report = TokenActivity(self.tokens, self.counters, self.clock)

    def issue(self, agent: str, expires_in: timedelta | None = None) -> str:
        request = IssueAgentTokenRequest(
            agent=agent,
            ceiling=INTERNAL,
            scopes=frozenset({Scope.READ}),
            expires_at=self.clock.now() + expires_in if expires_in else None,
        )
        return IssueAgentToken(self.tokens, self.clock, self.ids).execute(request, OWNER).token_id


def agent(token_id: str | None) -> AccessContext:
    return AccessContext(principal=Agent("codex"), ceiling=INTERNAL, scopes=frozenset({Scope.READ}), token_id=token_id)


def test_a_use_is_counted_for_the_credential_of_the_caller() -> None:
    kit = Kit()
    kit.record.execute(agent("token-a"))
    kit.record.execute(agent(None))
    kit.record.execute(OWNER)
    assert kit.counters.uses == [("token-a", kit.clock.now())]


def test_a_failing_counter_never_reaches_the_caller() -> None:
    kit = Kit()

    def broken(token_id: str, at: object) -> None:
        raise RuntimeError("fixture")

    kit.counters.record = broken  # type: ignore[method-assign]
    kit.record.execute(agent("token-a"))


def test_the_report_lists_every_token_with_its_state_and_activity_without_ids() -> None:
    kit = Kit()
    active = kit.issue("claude-code")
    revoked = kit.issue("codex")
    kit.issue("gpt", timedelta(minutes=1))
    RevokeAgentToken(kit.tokens, kit.clock).execute(RevokeAgentTokenRequest(token_id=revoked), OWNER)
    kit.record.execute(agent(active))
    kit.clock.advance(timedelta(minutes=2))
    rows = {row.agent: row for row in kit.report.execute(TokenActivityRequest(), OWNER)}
    assert [rows[name].state for name in ("claude-code", "codex", "gpt")] == ["active", "revoked", "expired"]
    assert rows["claude-code"].usage_known
    assert rows["claude-code"].last_used is not None
    assert sum(rows["claude-code"].hourly) == 1
    assert rows["codex"].last_used is None
    assert rows["codex"].hourly == (0,) * 24
    assert all(active not in repr(row) and revoked not in repr(row) for row in rows.values())


def test_unavailable_counters_leave_the_tokens_listed() -> None:
    kit = Kit()
    kit.issue("codex")
    kit.counters.unavailable = True
    (row,) = kit.report.execute(TokenActivityRequest(), OWNER)
    assert (row.usage_known, row.last_used, row.hourly) == (False, None, ())


@pytest.mark.parametrize(
    "context", [ctx(Sensitivity.RESTRICTED), ctx(Sensitivity.INTERNAL, owner=True), ctx(Sensitivity.PUBLIC, owner=True)]
)
def test_only_a_restricted_owner_reads_the_report(context: AccessContext) -> None:
    with pytest.raises(PermissionDenied):
        Kit().report.execute(TokenActivityRequest(), context)
