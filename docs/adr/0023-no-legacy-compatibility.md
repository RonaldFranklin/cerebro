# ADR-0023: Drop compatibility with the retired Inbox

- **Status:** accepted
- **Date:** 2026-11-26
- **Supersedes:** the preservation and compatibility requirements of [ADR-0020](0020-direct-agent-writes.md),
  [ADR-0018](0018-inbox-admission.md) and earlier ADRs: legacy tokens and `propose` grants, sealed proposal rows,
  in-place state upgrades, retired quota keys and the attribution of Inbox-era commits.

## Context

ADR-0020 replaced the Inbox with validated direct writes and asked every later release to keep what the Inbox
left behind: the `proposals` table and its migrations, the `propose` scope, the `proposals_*` settings, commits
with `Proposed-by` / `Curated-by` / `Approved-by` / `Proposal-Id` trailers (owner edits still wrote them) and
history parsing for them. That code existed only to read data from earlier releases.

The owner confirmed on 2026-11-26 that no real instance and no data from those releases exist. Keeping the
compatibility code costs review time and attack surface, and it holds rules that no deployment needs.

## Decision

- **One state schema.** `state.sqlite` has a single schema (version 5) with tokens, login attempts and write
  attempts. There is no `proposals` table and no migration. A new empty file is initialised. Any other version,
  including 1-4 from earlier releases, is refused with `StateSchemaError` naming the path and version and telling
  the owner to move the file aside and issue new tokens. The file is never deleted or upgraded and its contents
  (schema, version and rows) are not modified; opening it may still switch the SQLite header to WAL mode.
- **Scopes.** `read`, `write` and `core` only. `propose` is no longer a scope; a stored row naming it is a damaged
  row.
- **Settings.** `proposals_per_hour` and `proposals_pending` are unknown keys and fail configuration loading.
- **One commit format.** Every applied change ends with a single `Written-by` trailer: the authenticated agent
  name for agent writes, `owner` for owner edits. Agent creates keep their replay trailers until a later ADR
  replaces them. The owner editor only updates existing notes; the unreachable owner create path is removed.
- **History.** Recent changes recognise only the `Written-by` format. Commits with Inbox-era trailers or any
  other message are not attributed and are not listed.
- **Removed vocabulary.** The curator identity (`curator/...`), the `possible_duplicate` violation code and the
  proposal-only request fields are gone from the domain and adapters.

## Consequences

The code base loses the migration, scope, settings and parsing paths that only served earlier data. A state
file from an earlier release cannot be opened; the owner moves it aside and reissues tokens. Content history
written by the Inbox stays in git untouched but no longer appears in Recent changes. If an instance with old data
ever surfaces, a one-off migration script outside the application is the recovery path, not runtime support.

## Alternatives considered

- **Keep the compatibility code:** no data needs it, and every rule it carries must still be reviewed.
- **Upgrade old state in place, dropping proposals:** silently rewrites a file the owner may want to inspect;
  refusing with a clear message is safer and simpler.
- **Delete an old state file automatically:** destroys the only copy of token records without consent.
