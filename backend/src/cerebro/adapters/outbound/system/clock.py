"""Wall-clock adapter for the ``Clock`` port."""

from datetime import UTC, datetime


class UtcClock:
    """The current time as a timezone-aware UTC ``datetime``."""

    def now(self) -> datetime:
        return datetime.now(UTC)
