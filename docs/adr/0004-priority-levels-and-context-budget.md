# ADR-0004: Priority levels and context budgets

- **Status:** accepted, amended by [ADR-0020](0020-direct-agent-writes.md) (no curator: summary quality is the writer's responsibility) and [ADR-0025](0025-core-word-budget.md) (combined P0 budget)
- **Date:** 2026-11-24

## Context

The main cost of Cérebro is tokens in agents' context windows. Loading large prompt files whole on every session
is what Cérebro replaces. Cost per query must stay flat as the knowledge base grows.

## Decision

- Four priority levels: P0 (core, always loaded), P1 (hubs), P2 (regular notes), P3 (reference/archive, only on
  explicit search).
- Progressive disclosure: `overview → search (summaries only) → read (whole note or one section)`.
- Budgets enforced by the domain:
  - all P0 notes together ≤ 1,500 words;
  - hubs ≤ 800 words;
  - notes soft limit 1,500 words, hard limit 3,000;
  - `summary` 40–300 characters, mandatory.
- Search never returns bodies.
- The MCP surface stays small (see ADR-0014) because tool descriptions are loaded in every session.
- P0 content should be stable so agents benefit from prompt caching.

## Consequences

- Typical lookup ≈ 2–3k tokens regardless of size.
- Summary quality becomes critical; the curator is responsible for it.
- Large topics must be split into several notes linked from a hub.

## Alternatives considered

- Returning full notes in search: simpler for agents, but cost grows with result size.
- No budgets, rely on discipline: P0 would inflate over time.
