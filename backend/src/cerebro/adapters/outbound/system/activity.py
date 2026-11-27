"""The activity feed held in this process's memory (ADR-0031). Lost on restart; never written anywhere.

Once ``bind`` names the server's event loop, publishing from a worker thread only hands the event to that loop, which
owns the feed's state; subscriptions are made and read on it. Unbound (tests, startup) events apply directly.
"""

import contextlib
from collections import deque
from collections.abc import Callable
from typing import Final

from cerebro.application.activity import ActivityEvent
from cerebro.domain.access import AccessCeiling

RETAINED: Final = 20
Dispatch = Callable[[Callable[[], None]], object]


class _Subscription:
    def __init__(self, ceiling: AccessCeiling, wake: Callable[[], None], owner: set[_Subscription]) -> None:
        self._ceiling = ceiling
        self._wake = wake
        self._owner = owner
        self._queue: deque[ActivityEvent] = deque(maxlen=RETAINED)

    def offer(self, event: ActivityEvent) -> bool:
        if not self._ceiling.allows(event.sensitivity):
            return False
        self._queue.append(event)
        return True

    def notify(self) -> None:
        # A subscriber that cannot be woken must not stop the others.
        with contextlib.suppress(Exception):
            self._wake()

    def take(self) -> tuple[ActivityEvent, ...]:
        events = []
        while self._queue:
            events.append(self._queue.popleft())
        return tuple(events)

    def close(self) -> None:
        self._owner.discard(self)
        self._queue.clear()


class MemoryActivityFeed:
    def __init__(self) -> None:
        self._retained: deque[ActivityEvent] = deque(maxlen=RETAINED)
        self._subscribers: set[_Subscription] = set()
        self._dispatch: Dispatch = lambda action: action()

    def bind(self, dispatch: Dispatch) -> None:
        self._dispatch = dispatch

    def publish(self, event: ActivityEvent) -> None:
        # A closing event loop refuses the handoff: the event is lost with the process.
        with contextlib.suppress(RuntimeError):
            self._dispatch(lambda: self._apply(event))

    def subscribe(self, ceiling: AccessCeiling, wake: Callable[[], None]) -> _Subscription:
        subscription = _Subscription(ceiling, wake, self._subscribers)
        for event in tuple(self._retained):
            subscription.offer(event)
        self._subscribers.add(subscription)
        return subscription

    def _apply(self, event: ActivityEvent) -> None:
        self._retained.append(event)
        for subscription in tuple(self._subscribers):
            if subscription.offer(event):
                subscription.notify()
