# ADR-0002: Markdown + git as source of truth, SQLite as derived index

- **Status:** accepted
- **Date:** 2026-11-24

## Context

Notes must be durable, human-readable, versioned, reversible (agents may write bad content) and easy to back
up. Agents and the UI need fast search and graph queries.

## Decision

- Notes are Markdown files with YAML frontmatter in a git repository (the content repository).
- Search (FTS5) and graph data live in a SQLite database that is **derived**: it can be deleted and rebuilt
  from the content repository at any time (`RebuildIndex`).
- Operational state that is not knowledge (proposals, decisions, tokens) lives in a separate SQLite file and
  is backed up.

## Consequences

- Full history, diff and revert for free; every change is an auditable commit.
- The index never needs a backup; corruption is fixed by rebuilding.
- The index on the server contains decrypted text of restricted notes; it must stay on the server and is
  excluded from backups.
- Writes are serialized through the application (single writer) to avoid git conflicts.

## Alternatives considered

- Database as the only store (Postgres/SQLite): easier querying, but loses plain-file portability and git
  history, and makes backups opaque.
- Embedding/vector DB as primary store: unnecessary now; can be added later as an extra index.
