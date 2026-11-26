from collections.abc import Callable
from datetime import datetime, timedelta
from threading import Lock


class FakeWriteAttemptStore:
    def __init__(self) -> None:
        self._reservations: dict[str, dict[int, datetime]] = {}
        self._lock = Lock()

    def reserve(self, token_id: str, now: datetime, limit: int) -> Callable[[], None] | None:
        with self._lock:
            times = self._reservations.setdefault(token_id, {})
            if sum(moment > now - timedelta(hours=1) for moment in times.values()) >= limit:
                return None
            if len(times) >= limit:
                slot = min(times, key=times.__getitem__)
                previous = times[slot]
            else:
                slot = max(times, default=0) + 1
                previous = None
            times[slot] = now

        def release() -> None:
            with self._lock:
                if times.get(slot) == now:
                    if previous is None:
                        times.pop(slot)
                    else:
                        times[slot] = previous

        return release
