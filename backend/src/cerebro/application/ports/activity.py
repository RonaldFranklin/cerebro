"""The in-memory feed of agent activity (ADR-0031)."""

from collections.abc import Callable
from typing import Protocol

from cerebro.application.activity import ActivityEvent, ActivitySubscription
from cerebro.domain.access import AccessCeiling


class ActivityFeed(Protocol):
    def publish(self, event: ActivityEvent) -> None:
        """Keep the event among the retained ones and queue it for each subscriber whose ceiling covers it. It
        never blocks the publisher."""
        ...

    def subscribe(self, ceiling: AccessCeiling, wake: Callable[[], None]) -> ActivitySubscription:
        """A bounded queue that starts with the retained events within ``ceiling``. Events above it are filtered
        before the queue, so they never take the place of a visible one; a full queue drops its oldest."""
        ...
