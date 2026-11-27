"""``RecordTokenUse``: count one authenticated agent request (ADR-0031)."""

import contextlib

from cerebro.application.ports.system import Clock
from cerebro.application.ports.usage import UsageCounters
from cerebro.domain.access import AccessContext


class RecordTokenUse:
    """Called once for each request an adapter has authenticated. It changes nothing about authentication, and its
    failures never reach the caller."""

    def __init__(self, counters: UsageCounters, clock: Clock) -> None:
        self._counters = counters
        self._clock = clock

    def execute(self, ctx: AccessContext) -> None:
        if ctx.token_id is None:
            return
        with contextlib.suppress(Exception):
            self._counters.record(ctx.token_id, self._clock.now())
