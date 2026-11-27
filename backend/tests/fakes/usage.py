from collections.abc import Mapping
from datetime import datetime, timedelta

from cerebro.application.dto import USAGE_HOURS, TokenUsage

_HOUR = timedelta(hours=1)


class FakeUsageCounters:
    def __init__(self) -> None:
        self.uses: list[tuple[str, datetime]] = []
        self.unavailable = False

    def record(self, token_id: str, at: datetime) -> None:
        if not self.unavailable:
            self.uses.append((token_id, at))

    def summary(self, now: datetime) -> Mapping[str, TokenUsage] | None:
        if self.unavailable:
            return None
        current = now.replace(minute=0, second=0, microsecond=0)
        result = {}
        for token_id in {token for token, _ in self.uses}:
            times = [at for token, at in self.uses if token == token_id]
            hourly = [0] * USAGE_HOURS
            for at in times:
                age = (current - at.replace(minute=0, second=0, microsecond=0)) // _HOUR
                if 0 <= age < USAGE_HOURS:
                    hourly[USAGE_HOURS - 1 - age] += 1
            result[token_id] = TokenUsage(last_used=max(times), hourly=tuple(hourly))
        return result
