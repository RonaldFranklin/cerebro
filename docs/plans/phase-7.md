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

## Delivery report

| PR | Delivered | Decisions for review |
|---|---|---|
| [#68](https://github.com/RonaldFranklin/cerebro/pull/68) | ADR, plan, architecture and roadmap | Issue write grants explicitly. Generate unpredictable note IDs server-side. Only a full, unredacted read exposes the update hash. Count attempts per authenticated token across restarts. |
| [#69](https://github.com/RonaldFranklin/cerebro/pull/69) | Direct-write use case, Git attribution, receipts and persistent quota | A create receipt is recoverable from the original Git commit; its content fingerprint is authenticated and encrypted. Replaying an identical request returns the original ID and revision after current authorization. Changed or stale requests never overwrite another edit. Git is authoritative if index maintenance is interrupted. |
| [#70](https://github.com/RonaldFranklin/cerebro/pull/70) | Scoped MCP writer and packaged guide, three-line project snippet and CLI cutover | Both transports report bounded field codes and corrective hints. Authenticated invalid requests consume quota, including unknown fields. A legacy proposal grant is inert. The owner can issue a new read/write grant explicitly. |
| [#71](https://github.com/RonaldFranklin/cerebro/pull/71) | Recent changes with actor, timestamp, note and diff; retirement of legacy review and curator paths | Derive history from content Git. Preserve historical commits, sealed state and backups; old proposals are never executed. The history API and screen require a private owner session. |

### Security and acceptance

The installed MCP SDK creates by HTTP and updates through the stdio bridge with separate authenticated agents. It checks the Git parent and authorship, replay, stale hashes, safe scanner feedback and the per-token limit. A rejected attempt can consume a quota slot, but produces no note or pending task. Agents receive no writer authority from their old proposal grants. Restricted data remains subject to the accepting listener, token ceiling, encryption and full-read checks.

The browser demo uses a disposable content repository and fixture credentials. After a direct write, Recent changes shows the committed result without an approval step. Owner edits and earlier attributed commits retain their original provenance. The owner can follow the [real-client walkthrough](../agent-integration.md#inspect-the-same-client-writes-in-recent-changes) to inspect both clients' writes in the same private browser instance. Real Claude Code and Codex sessions remain an owner-run check. The manual checkpoints for Phases 3–5 remain recorded in their respective delivery reports.

**Final gates.** 1,995 backend tests passed with 98.07% combined domain/application coverage, alongside Ruff lint/format, strict mypy and four import contracts. Chromium passed 44 component tests and 10 installed-service HTTPS end-to-end tests. The earlier baseline included tests for the retired proposal/curator implementation. [Recent changes screenshot](../screenshots/phase-7/recent-changes.png)

An additional interactive WSL run used the SDK to fetch the guide, create a note, read its hash and update it. The private HTTPS history returned the two matching Git revisions, actor and note, and the expected update diff. The printed certificate fingerprint matched and shutdown removed the fixture.
