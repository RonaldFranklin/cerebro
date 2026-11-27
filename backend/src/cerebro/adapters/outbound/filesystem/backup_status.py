"""Read the record ``deploy/backup.py`` writes in the state directory (ADR-0030)."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from cerebro.adapters.outbound.filesystem.safeio import read_regular_file
from cerebro.application.dto import LastBackup
from cerebro.application.history import is_revision

MAX_BYTES: Final = 512
_FIELDS: Final = frozenset({"completed_at", "content_revision"})


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("duplicate field")
    return fields


class BackupStatusFile:
    def __init__(self, path: Path) -> None:
        self._path = path

    def last(self) -> LastBackup:
        try:
            data = read_regular_file(self._path.parent, self._path.name, MAX_BYTES)
        except FileNotFoundError:
            return LastBackup(state="never")
        except OSError:
            return LastBackup(state="unreadable")
        try:
            record = json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
            if not isinstance(record, dict) or record.keys() != _FIELDS:
                raise ValueError("unexpected fields")
            moment, revision = record["completed_at"], record["content_revision"]
            if not isinstance(moment, str) or not isinstance(revision, str) or not is_revision(revision):
                raise ValueError("unexpected values")
            completed = datetime.fromisoformat(moment)
            if completed.utcoffset() != timedelta(0):
                raise ValueError("a UTC time is required")
        except ValueError, RecursionError:
            return LastBackup(state="unreadable")
        return LastBackup(state="recorded", completed_at=completed, content_revision=revision)
