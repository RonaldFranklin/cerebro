# ADR-0014: Small MCP tool surface

- **Status:** accepted
- **Date:** 2026-11-24

## Context

MCP tool names and descriptions are loaded into the agent's context in every session. Many tools, or verbose
descriptions, cost tokens even when unused.

## Decision

Expose exactly five tools, with short descriptions:

| Tool | Input | Output |
|---|---|---|
| `cerebro_overview` | — | P0 core text + list of areas and hubs (id, title, summary) |
| `cerebro_search` | `query`, optional `area`, `type`, `priority`, `status`, `include_history`, `limit` (default 5) | id, title, type, area, priority, status, summary |
| `cerebro_read` | `id`, optional `section` | note (frontmatter essentials + body or section) |
| `cerebro_neighbors` | `id`, optional `depth` (default 1, max 2) | linked notes as id, title, summary, relation |
| `cerebro_propose` | proposal (domain model §7) | proposal id + validation result |

- Read tools require scope `read`; `cerebro_propose` requires scope `propose`.
- All results are filtered by the effective ceiling.
- When the service is unavailable, agents see a clear "Cérebro offline" error.
- Adding a tool requires a new ADR.

## Consequences

- Fixed, small per-session overhead.
- Advanced queries belong to the HTTP API/UI, not to MCP.

## Alternatives considered

- MCP resources instead of tools for notes: less consistently supported across clients today.
