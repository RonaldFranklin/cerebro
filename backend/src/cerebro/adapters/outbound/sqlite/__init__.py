"""SQLite adapters.

``index.sqlite`` is a **disposable** derived index: search (FTS5) and graph. If it is missing, corrupt or written by
another schema version it is recreated empty, and ``RebuildIndex`` fills it again. It is never migrated.

``state.sqlite`` is **not** disposable: tokens, durable attempt windows and sealed legacy proposal rows. An unknown
schema version or a damaged file raises a clear error and the file is never deleted or repaired.
"""

from cerebro.adapters.outbound.sqlite._db import IndexUnavailableError
from cerebro.adapters.outbound.sqlite._state_db import (
    StateCorruptionError,
    StateFileError,
    StateSchemaError,
    StateStoreError,
)
from cerebro.adapters.outbound.sqlite.graph import SqliteGraphIndex
from cerebro.adapters.outbound.sqlite.search import SqliteSearchIndex
from cerebro.adapters.outbound.sqlite.tokens import SqliteTokenStore

__all__ = [
    "IndexUnavailableError",
    "SqliteGraphIndex",
    "SqliteSearchIndex",
    "SqliteTokenStore",
    "StateCorruptionError",
    "StateFileError",
    "StateSchemaError",
    "StateStoreError",
]
