# ADR-0012: Environments and server-only runtime

- **Status:** accepted, amended by [ADR-0020](0020-direct-agent-writes.md) (no curator batches to schedule)
- **Date:** 2026-11-24

## Context

The owner develops in WSL on a workstation and runs services on a home server with Docker Compose. The server
shuts down automatically every day at 01:00 local time and is powered on manually.

## Decision

- **dev:** WSL on the workstation. Tests, local runs and a dev instance with sample content live here.
- **hml** and **prod:** on the server, as Docker Compose projects `cerebro-hml` and `cerebro-prod`, with
  separate volumes, ports and content.
  - hml uses sample/fixture content or a copy without restricted notes.
  - The same image (`cerebro-backend:git-<sha>`) is validated in hml and then promoted to prod; keep the
    current and previous validated images for rollback.
- Nothing of Cérebro runs on the workstation in hml/prod; agents there connect to the server.
- When the server is off, Cérebro is unavailable. Agents receive a clear "offline" error and continue.
  No local mirror.
- Scheduled jobs (reindex, backups, curator batches) never run between 00:00 and 01:00 local time.
- Compose conventions: explicit `-p`, `config --quiet` before `up`, healthchecks, restart policy, log
  rotation, no database ports published on the host.

## Consequences

- A single source of truth with no sync conflicts.
- Availability is tied to server uptime; accepted.

## Alternatives considered

- Read-only local mirror on the workstation: would copy restricted notes to the workstation and add sync
  complexity.
