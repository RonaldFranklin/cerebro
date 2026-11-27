"""``TokenActivity``: the owner's view of every agent credential and its recent use."""

from datetime import datetime
from typing import Literal

from cerebro.application.access import require_owner
from cerebro.application.dto import USAGE_HOURS, TokenActivityRequest, TokenActivityRow
from cerebro.application.errors import PermissionDenied
from cerebro.application.ports.system import Clock
from cerebro.application.ports.tokens import TokenStore
from cerebro.application.ports.usage import UsageCounters
from cerebro.domain.access import AccessContext
from cerebro.domain.agent_token import AgentToken
from cerebro.domain.enums import Sensitivity


class TokenActivity:
    """Owner only, with the ceiling ``restricted``. Ids stay inside: they join a credential to its counters."""

    def __init__(self, tokens: TokenStore, counters: UsageCounters, clock: Clock) -> None:
        self._tokens = tokens
        self._counters = counters
        self._clock = clock

    def execute(self, request: TokenActivityRequest, ctx: AccessContext) -> tuple[TokenActivityRow, ...]:
        require_owner(ctx, "read token activity")
        if ctx.ceiling.level is not Sensitivity.RESTRICTED:
            raise PermissionDenied("read token activity below the restricted ceiling")
        now = self._clock.now()
        usage = self._counters.summary(now)
        rows = []
        for token in self._tokens.all():
            used = None if usage is None else usage.get(token.id)
            rows.append(
                TokenActivityRow(
                    agent=token.agent,
                    ceiling=token.ceiling,
                    scopes=token.scopes,
                    state=_state(token, now),
                    usage_known=usage is not None,
                    last_used=used.last_used if used else None,
                    hourly=() if usage is None else used.hourly if used else (0,) * USAGE_HOURS,
                )
            )
        return tuple(rows)


def _state(token: AgentToken, now: datetime) -> Literal["active", "revoked", "expired"]:
    if token.revoked_at is not None:
        return "revoked"
    return "expired" if token.expires_at is not None and now >= token.expires_at else "active"
