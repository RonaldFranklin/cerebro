"""SQLite adapters.

``index.sqlite`` (this PR) is a **disposable** derived index: search (FTS5) and graph. If it is missing, corrupt or
written by another schema version it is recreated empty, and ``RebuildIndex`` fills it again. It is never migrated.
"""

from cerebro.adapters.outbound.sqlite._db import IndexUnavailableError
from cerebro.adapters.outbound.sqlite.graph import SqliteGraphIndex
from cerebro.adapters.outbound.sqlite.search import SqliteSearchIndex

__all__ = ["IndexUnavailableError", "SqliteGraphIndex", "SqliteSearchIndex"]
