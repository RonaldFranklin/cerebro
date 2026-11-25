# ADR-0015: Hub membership is computed at read time

- **Status:** accepted
- **Date:** 2026-11-24

## Context

Hubs (P1) are maps of an area: they list the notes that belong to it with one-line summaries. If the member list
were written into the hub file, every new, renamed or superseded note would require a second edit to the hub,
lists would drift from reality, and a hub mixing notes of different sensitivity would leak the existence of
restricted notes to callers with a lower ceiling.

## Decision

- A hub note stores **only its introduction**: what the area covers and how to navigate it. The 800-word hub
  budget ([ADR-0004](0004-priority-levels-and-context-budget.md)) applies to this introduction.
- Membership is defined by the `hub` field of the other notes. It is never written into the hub file.
- When a hub is read, the member list is built from the index at read time and filtered by the caller's
  effective ceiling before anything is returned. Members with status `superseded` or `archived` are listed
  only with `include_history`.
- Each member entry contains `id`, `title`, `type`, `priority`, `status` and `summary`; never the body.
- A hub may itself set `hub` to a parent hub (sub-area); it then appears in the parent's member list.
- Hub chains (a hub's `hub`, then its parent's `hub`, …) must not contain cycles and have a maximum depth of
  **3 hubs** (an area hub plus at most two levels of sub-area hubs). Both rules are validated in the domain.

## Consequences

- Adding a note to an area is a single edit (its `hub` field); hub lists cannot drift.
- Restricted members are invisible to lower ceilings, as everywhere else ([ADR-0005](0005-sensitivity-and-access-ceiling.md)).
- Reading a hub needs the index (`GraphIndex`/`SearchIndex`), not only the note file.
- The owner cannot hand-curate the order of members; the order is deterministic (priority, then title).
  Curated reading paths go in the introduction as wikilinks.

## Alternatives considered

- Member list written in the hub body and kept in sync by the curator: drifts, doubles every write, leaks
  restricted ids into a lower-sensitivity file.
- Hand-written list validated against the index: still two edits per change, and filtering by ceiling would
  require rewriting the hub at read time anyway.
