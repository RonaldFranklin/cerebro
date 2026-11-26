from datetime import datetime, timedelta
from threading import Lock


class FakeWriteAttemptStore:
    def __init__(self) -> None:
        self._reservations: dict[str, list[datetime]] = {}
        self._lock = Lock()

    def reserve(self, token_id: str, now: datetime, limit: int) -> bool:
        with self._lock:
            times = self._reservations.setdefault(token_id, [])
            if sum(moment > now - timedelta(hours=1) for moment in times) >= limit:
                return False
            if len(times) >= limit:
                times.remove(min(times))
            times.append(now)
            return True

    def release(self, token_id: str, reserved_at: datetime) -> None:
        with self._lock:
            times = self._reservations.get(token_id, [])
            if reserved_at in times:
                times.remove(reserved_at)
