# ADR-0018: Validate Inbox submissions before durable admission

- **Status:** superseded by [ADR-0020](0020-direct-agent-writes.md)
- **Date:** 2026-11-25

## Context

Phase 4 opens proposal submission to authenticated agents. A token can produce proposals faster than a single
owner can review them. In the previous multi-step composition, an interrupted validation could leave an
encrypted copy of a subsequently rejected submission in SQLite or its WAL. Existing clients can also hold
different tokens issued under the same agent name.

## Decision

- Bind a submission to the authenticated token's public id for quotas and its agent name for attribution.
  Accept neither identity from the request. Enforce the effective ceiling and `propose` scope. Discover tools
  according to scope and recheck scope on invocation. The four read tools continue to require `read`.
- Run SubmitProposal, deterministic validation and NullCurator in order in request-private memory. The server
  invokes the owner-controlled validation and curation steps internally. Keep the submission's access checks,
  the curator ceiling and the restricted-content path defined in ADR-0016. Publish the first durable row only
  when a scrubbed validation rejection or a reviewable proposal is ready. Use the existing sealed format for
  proposal content. A failure before admission discards the in-memory work.
- Count admitted proposals per token in a rolling hour, including scrubbed rejections. Count every nonterminal
  proposal as pending, including an approval awaiting application. Default to 20 admissions per hour and 10
  pending per token. These limits allow a short working session while bounding the review queue. Both are
  configurable positive integers. Check both limits and insert the row with its token association in one
  serialized SQLite transaction. At either limit return one fixed error and commit no writes. A proposal
  admitted exactly one hour ago falls outside the window. Tokens with the same agent name have separate budgets.
- Migrate the backed-up state in place. Retain earlier proposals and decisions with unknown token attribution.
  Start enforcing quotas on token-attributed admissions. Inferring an old token from an agent name would charge
  the wrong credential when names are shared.
- Return the proposal id and a bounded validation outcome. Keep detailed findings, candidate notes, curator
  decisions and duplicate matches within the owner-controlled pipeline. Preserve indistinguishable hidden and
  missing targets. Apply the same envelope, authentication and logging rules on both transports.
- The owner can preview, amend and approve a reviewable create or update through the CLI. Require the inspected
  revision for edits and approvals. Validate edits before storing them, expose the new revision for review,
  and preserve the source and curator in the audit. ApplyProposal remains the commit boundary.

## Consequences

Admission survives restart and concurrent requests share one budget across listeners and processes. A validation
rejection leaves only its scrubbed audit record, including when the process stops between steps. An interrupted
request before the first durable write leaves no proposal. Existing Phase 3 backups retain their historical
contents. The new policy governs submissions after migration.

## Alternatives considered

Process-local counters lose history on restart and split the budget between workers. Counting by agent name
couples independent credentials. Storing a received row before validation leaves a durable payload even if a
later transaction replaces it.
