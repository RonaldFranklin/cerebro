# ADR-0006: Write path — Inbox, validation, curator, approval

- **Status:** superseded by [ADR-0020](0020-direct-agent-writes.md)
- **Date:** 2026-11-24

## Context

If every agent could write freely, the knowledge base would fill with duplicates, low-value notes and
inconsistent formats. The owner wants an internal agent to keep Cérebro organized, with the option to allow
direct writes in the future.

## Decision

- External agents have only the `propose` capability. Proposals go to the Inbox.
- Pipeline: `received → validated → curated → (owner) approved/rejected → applied`.
  - **Validation** is deterministic and runs first (schema, budgets, secret scan, duplicates).
  - **Curation** is done by the internal curator through the `Curator` port; it returns a `Decision` with a
    diff and never writes.
  - **Approval** is manual for every decision at first.
  - **Application** is done only by the `ApplyProposal` use case, producing a git commit with
    `Proposed-by`, `Curated-by`, `Approved-by` and `Proposal-Id` trailers.
- The owner may edit notes directly (`OwnerEditNote`), still validated and committed with the owner as author.
- "Direct write" for agents, when enabled in the future, is an **autonomy policy** (auto-approve rules), not a
  separate code path.
- Proposals and decisions are kept for audit.

## Consequences

- Quality and consistency are controlled in one place.
- Every change is attributable and reversible.
- The owner must review proposals until autonomy is raised; the UI must make this fast.

## Alternatives considered

- Agents write to a branch and the owner merges: relies on git tooling for review, no curation step.
- Direct writes with periodic cleanup: degrades quality between cleanups.
