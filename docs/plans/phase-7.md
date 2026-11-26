# Phase 7 — Direct agent writes

The owner authorized planning, implementation and merges on 2026-11-26, including the ADR and security
changes. Use one branch per step from updated main, independent diff review, green CI and
`scripts/merge-pr.sh`. PR #67 was closed without merging. Phase 6 deployment follows this phase.

## Delivery sequence

| Step | Deliverable | Verification |
|---|---|---|
| 1. Decision and plan | [ADR-0020](../adr/0020-direct-agent-writes.md), updated architecture, domain model, roadmap and repository instructions. | Independent decision review and CI. |
| 2. Core and persistence | Implement authenticated create/update, explicit token scope, durable per-token attempt limit, content hash checks, attribution and index recovery. Use in-memory fakes first and existing writer serialization, Git, encryption and SQLite adapters. | Unit and shared contract tests. Include old state, two tokens sharing an agent name, concurrent and failed calls, restart, secret rejection, ceilings and persisted Git trailers. |
| 3. MCP and guide | Replace proposal submission with scoped write and on-demand guide tools. Supply actionable safe errors, scoped full-read update hashes, a packaged English guide and a project AGENTS.md snippet of at most five lines. Retire proposal admission and approval entry points at cutover. | Installed SDK discovery and invocation over supported transports, read-only and legacy-token denials, projected errors, optimistic concurrency, guide availability in an installed package. |
| 4. Owner history and acceptance | Replace Inbox navigation with Recent changes, add bounded private history list/detail, link to note and diff, remove unused proposal and curator paths, and update real-client instructions. | Fake/real history contracts, browser component and installed HTTPS E2E tests, upgrade and regression checks, delivery report with manual checks distinguished from automation. |

## Acceptance

- A freshly authorized agent searches, consults the guide when writing, creates a synthetic note and
  immediately reads the committed result. Simulate a lost response after commit: replay identical
  input for the original receipt and changed input for a conflict, with no additional commit. A second agent reads and updates it with its base hash.
  Inspect Git for one commit per change and authenticated authorship. A stale update preserves the winner.
- Invalid schema, missing links, oversize text and synthetic secrets yield bounded actionable feedback.
  Confirm rejected payloads are absent from Git, state and logs. Exercise cross-ceiling targets,
  inbound and outbound links, redacted reads and concurrent requests. Compare missing and hidden
  links in code spans, fenced blocks, metadata and aliases for identical errors and commit outcomes. A quota refusal changes no content.
  Validate that legacy grants cannot write and historical records survive cutover.
- The guide covers durable decisions, configuration, architecture, working procedures and project
  conventions. Exclude secrets, transient conversations, logs, obvious facts and duplicates. Include
  search-before-create, update preference, English, summary, area/type/priority and actual size budgets.
- From Recent changes, identify the agent, timestamp and note for each write, open the note and inspect
  the diff. Verify private owner session requirements on list and detail, forbidden revisions, escaped
  malicious text and bounded rendering. Access to old notes must respect current and historical visibility.
- Document an isolated Claude Code and Codex walkthrough with explicit grants, guide discovery, create,
  update, stale-base handling and inspection in Git and the browser. Record which clients actually ran.

## Status

**Implemented.** Step 1 was approved in PR #68. The step 2 application writer runs under the shared
writer lock. Creates record an opaque token-scoped request key and an encrypted content fingerprint in the same Git commit
as the note. The durable receipt consists of its note id and original revision. Feedback from validation
accompanies a fresh write. An unchanged update returns a bounded finding without a commit. The rolling-hour limit defaults to 20 and uses the state database.
Before a write narrows visibility, the writer evicts the older search and graph projections. Startup
compares both index revisions with HEAD and rebuilds after interrupted indexing.

**Tested.** Application tests and shared fake/real storage contracts exercise scope, quota, replay,
ceilings and Git attribution. Real-container tests cover two writers sharing a base, interrupted indexing
and recovery on restart. MCP cutover, browser history and manual client acceptance are pending.
