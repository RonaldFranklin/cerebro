# ADR-0022: A `core` scope guards the global rules

- **Status:** accepted
- **Date:** 2026-11-26
- **Amends:** [ADR-0020](0020-direct-agent-writes.md), for authority over core and P0 notes.

## Context

`cerebro_overview` serves every current P0 note, in full, to every agent at the start of a session. These
notes are the global rules. Under ADR-0020 any `write` token could create a P0 note, rewrite or archive an
existing one, or supersede it. One misbehaving or prompt-injected agent could therefore change the
instructions every other agent receives. The only existing guard stopped a note from changing between the
core, hub and regular categories.

## Decision

Add a token scope `core`, issued explicitly by the owner (`cerebro token issue ... --scope core`) and only
to trusted agents.

- An agent without `core` cannot create or update a note when the stored version **or** the candidate has
  type `core` or priority `P0`. This covers creating a core note, editing, archiving, superseding or
  demoting one, and promoting a note to P0. The write fails with `core_scope_required` before any commit.
- A create replay is authorized the same way: the stored note and the replayed candidate are both checked.
- `core` is meaningless without `write`. A credential holding `core` without `write` is invalid: issuing
  one is refused and the domain entity rejects such a stored row. Refusing is clearer than a grant that
  silently authorizes nothing.
- Hubs (P1) and regular notes stay open to every `write` token.
- With `core`, the existing rules still apply: a note keeps its core, hub or regular category, and current
  P0 notes share the 1,500-word budget.
- The owner holds every scope, as before. Discovery of the write tools still depends on `write` only.

The overview is built only from current P0 notes and hub summaries. P0 notes reach the repository only
through `AgentWrite` (now gated) or the owner's `OwnerEditNote`; the search index is derived from
validated notes. A non-core agent can still change a note that a core note links to; the overview then
shows at most the redaction marker in place of that link, never the linked note's content.

## Consequences

The owner must reissue tokens for agents that should maintain core rules. A non-core agent that wants
to change a rule must ask the owner or a trusted agent. Token rows remain unauthenticated (accepted risk in
`docs/architecture.md`): whoever can write the state database can grant `core`, as they can grant `write`.

## Alternatives considered

- **Owner-only core notes:** simplest, but the owner wants trusted agents to maintain the rules.
- **Treating `core` as implying `write`:** hides the grant's real extent and makes a revoked write ambiguous.
- **Silently ignoring `core` without `write`:** leaves a misleading credential in the store.
- **Also guarding hubs:** hubs only index notes and are meant to grow with the corpus; the owner kept them open.
