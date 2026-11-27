"""What the host backup job reported last (ADR-0030)."""

from typing import Protocol

from cerebro.application.dto import LastBackup


class BackupStatus(Protocol):
    def last(self) -> LastBackup:
        """The last record the backup job left. Never raises for a missing or damaged record: ``never`` or
        ``unreadable``, carrying nothing of what was read."""
        ...
