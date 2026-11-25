# ADR-0016: The curator never receives restricted notes

- **Status:** accepted
- **Date:** 2026-11-25

## Context

The curator ([ADR-0007](0007-curator-provider-agnostic.md)) is an external LLM reached over the network. Anything
placed in its context leaves the owner's infrastructure. `restricted` notes are encrypted at rest
([ADR-0008](0008-encryption-of-restricted-notes.md)) precisely so that they do not travel; sending them (or a
proposal that quotes, links or rewrites them) to a third-party model would defeat that protection.

## Decision

- The curator context has a hard ceiling of `internal`: notes above it are never read into the context, never
  listed as candidates for merging, and never shown as link targets.
- A proposal that **creates, changes or links** a `restricted` note (target note, `hub`, `related`,
  `superseded_by`, or wikilinks in the proposal's text fields) is not sent to the curator. It goes straight to `needs_owner`, with the
  reason recorded in the decision.
- The rule is enforced in the `CurateProposal` use case, independently of which `Curator` adapter is configured,
  so a new adapter cannot bypass it.
- The owner reviews such proposals manually (the `NullCurator` path of [ADR-0007](0007-curator-provider-agnostic.md)).

## Consequences

- Restricted knowledge never reaches a third-party model, whatever the provider.
- Proposals touching restricted notes always need the owner; curator automation (Phase 7) does not cover them.
- The curator cannot suggest links to restricted notes, since it never sees them.

## Alternatives considered

- Redacting restricted notes before sending them: redaction is error-prone and the summary alone can leak.
- A per-adapter opt-in (e.g. a local model may see restricted notes): possible later through a new ADR; the
  default stays closed.
