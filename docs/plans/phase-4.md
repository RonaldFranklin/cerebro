# Phase 4 plan — Inbox

The owner authorized autonomous implementation on 2026-11-25 within the roadmap and accepted ADRs, including
merges after fresh review, resolution of findings and green CI. Phase 3 is approved. Its security checkpoint
awaits only the owner's real-client tests. [Phase 4 scope](../roadmap.md#-phase-4--inbox)

## 1. Pull requests

Open each branch from current main after the preceding PR merges. Use scripts/merge-pr.sh for every merge.
Status legend: ⬜ not started · 🟦 PR open · ✅ merged.

| # | Branch | Content and verification | Depends on | Status |
|---|---|---|---|---|
| 1 | `docs/phase-4-plan` | Record scope, approval, start status and ADR-0018. | SQLite correction #48 | ✅ #49 |
| 2 | `phase-4/token-quotas` | Carry authenticated token id alongside the audit name. Extend the admission port and fake, migrate existing SQLite state, configure limits and implement atomic admission. Contract-test boundary times, pending transitions, shared names, concurrency and restart. | 1 | ✅ #50 |
| 3 | `phase-4/inbox-intake` | Compose submission, validation and NullCurator with transient staging. Persist only the admissible final state. Support owner amendment of create/update candidates, revision-checked review and apply. Test secret and other validation rejections at the first durable write, interrupted intake, provenance, stale edits and validation after edits. | 2 | ✅ #51 |
| 4 | `phase-4/mcp-propose` | Add the fifth ADR-0014 tool to HTTP and dev stdio. Implement strict wire parsing, bounded fields, scope-aware discovery and dispatch, and a safe agent response. Exercise both transports and denial paths. | 3 | ✅ #52 |
| 5 | `test/phase-4-acceptance` | Drive the installed server with the official SDK and the owner's installed CLI, inspect the git commit, test a synthetic secret and both quotas. Extend the manual real-client runbook and record the phase report, roadmap and hand-off. | 4 | ✅ #53 |

## 2. Owner requirements and design decisions

1. **Token-level limits.** Start at 20 admissions per rolling hour and 10 pending per token. Expose both
   defaults through the instance configuration. Exercise each limit independently, then release pending capacity
   by rejecting or applying a proposal. Use the existing clock, deterministic fakes, distinct tokens with the
   same agent name and real competing SQLite connections. A refusal must leave the proposal, accounting and
   content stores untouched. [ADR-0018](../adr/0018-inbox-admission.md)
2. **Durable boundary.** Use the normal SubmitProposal → ValidateProposal → CurateProposal lifecycle in memory.
   Before any durable write, check schema, word budgets, secrets, links, duplicates and sensitivity.
   Duplicates retain the domain's warning policy. Curate valid submissions with NullCurator or the restricted
   policy branch. Test persistence by inspecting the objects presented to the durable adapter as well as its
   final rows. Validate with fixtures including a rejected body that passes the initial secret scan.
   Reconcile the Phase 2 residual-risk text in architecture and domain-model when PR 3 changes the wiring.
   [Write path](../adr/0006-write-path-inbox-curator.md) · [Curator ceiling](../adr/0016-curator-never-sees-restricted.md)
3. **Manual ownership.** Keep the current revision-aware list, preview, approve, reject and resume commands.
   Add an owner-only amendment for reviewable create/update content with bounded inputs. Reject invalid edits
   without saving. Preview the revised candidate before approval. Preserve base hash, submitted identity,
   curator and proposal id, and revalidate at apply. Test the persisted commit and all four audit trailers.
4. **Wire contract.** Parse the domain-model §7 input, including the closed nested suggested-metadata and
   source objects. Source accepts only optional session/context. Treat every unknown property, including a
   claimed agent or token id, as invalid. Keep the 64 KiB envelope. Declare and enforce per-field UTF-8 byte
   caps for body, title, rationale, summary, session and context, bounded enumeration/slug/id/digest fields
   and a bounded related-id array. Record the numerical caps alongside the implemented schema in PR 4 and
   test exact bounds, one byte over, nested fields and Unicode. Reject malformed envelopes before use cases.
   Return only the proposal id and a validation result safe at the caller's ceiling.
   [Tool surface](../adr/0014-mcp-tool-surface.md) · [Transport identity](../adr/0017-cli-and-mcp-transports.md)
5. **Scope and visibility.** A propose-only token can use the fifth tool. A read-only token keeps the four
   Phase 3 tools, and a token with both scopes can use all five. The server derives source.agent from the
   token, keeps credentials and payloads out of logs, and rejects hidden targets the same way as missing
   targets. Include references in metadata and body links in visibility tests.
   [Listener and token ceilings](../adr/0005-sensitivity-and-access-ceiling.md)

## 3. Dependencies and verification

Use the existing SDK, SQLite, Fernet, git and argparse. The plan introduces no package dependency.
Write domain/application tests first with fakes and share storage contract tests with SQLite. Each PR runs
locked install, Ruff lint and format, strict mypy, import-linter and pytest with coverage and ResourceWarning
enforcement. Record review decisions in its PR description.

## 4. Acceptance and hand-off

- Submit a synthetic proposal with an SDK client. Verify successful validation and the stored NullCurator decision at
  needs_owner before manual CLI approval. Inspect the candidate, then verify one apply commit and its Proposed-by, Curated-by, Approved-by and Proposal-Id trailers.
- Submit a fixture containing a fake private key, verify rejection and inspect the persisted audit projection.
  Exceed each configured limit, verify the fixed refusal and unchanged persistent state. Include restart,
  token isolation and concurrent admission in the supporting tests.
- Extend docs/agent-integration.md with exact, isolated instructions to propose in Claude Code and Codex and
  approve through the CLI. The owner runs the real clients. Keep all test content synthetic.
- Update this plan, the roadmap and AGENTS.md with merged PRs, tested outcomes, coverage, decisions and the
  security checkpoint. Record acceptance measurements after the tests run.


### MCP proposal fields (PR 4)

Pass the proposal itself as the tool arguments. The closed top-level object accepts `kind`, `title`, `body`,
`target`, `base_hash`, `suggested`, `rationale` and `source`. The kind selects the required fields per domain
model §7. Under `suggested`, accept `area`, `type`, `priority`, `sensitivity`, `summary`, `hub`, `related`
and `superseded_by`. Under `source`, accept only `session` and `context`. Optional fields may be omitted.
Both transports validate the following UTF-8 budgets before authentication or admission.

| Field | Limit |
|---|---|
| MCP envelope / proposal body | 64 KiB / 48 KiB |
| Title / suggested summary | 480 / 1,200 bytes, allowing four bytes per domain character |
| Rationale / session / context | 4,096 / 2,000 / 2,000 bytes |
| Target, hub, superseded_by, each related id | 80 ASCII kebab-case bytes |
| Related list / area and type slugs | 32 entries / 256 ASCII bytes each |
| Base hash / kind, priority, sensitivity | 64 lowercase hexadecimal bytes / closed domain enums |

The response exposes `proposal_id` and `valid`. Either quota returns the same fixed error, `Proposal limit reached.`
For a token with both scopes, a full `cerebro_read` includes the canonical `base_hash` of exactly the visible
body and editable fields. Hidden or missing references that yield the same projection produce the same hash.
Apply compares the supplied digest to the actual stored note, so a filtered view can conflict at owner review
or apply. Section reads and read-only credentials keep the Phase 3 projection.


## 5. Final report

**Delivered 2026-11-25.** PRs #49–#53 implement the five approved steps after the SQLite correction in
#48. The locked full suite passed with 2,192 tests, 99.45% branch-aware coverage of domain and application,
zero warnings, Ruff lint and format, strict mypy and four passing import contracts. No dependency was added.

**Tested.** The official MCP SDK authenticated to the installed server, submitted a synthetic create and
received only its id and validation outcome. The encrypted Inbox row contained successful validation and a
NullCurator decision before the owner's CLI preview. Approving that revision produced one git commit with
the proposed body and its proposer, curator, approver and proposal-id trailers. A second submission with a
synthetic private-key marker produced a scrubbed rejection. A test-only SQLite trigger preserved the first
insert so the test could inspect its status and decrypted payload. Refusals at the pending and hourly limits
returned the same error and left the content HEAD and an independent SQLite connection's data_version
unchanged. [Acceptance test](../../backend/tests/integration/test_phase4_acceptance.py)

### Decisions for review

| PR | Delivery decision |
|---|---|
| [#48](https://github.com/RonaldFranklin/cerebro/pull/48) | Deterministically close SQLite connections even during initialization failure. Fail pytest on ResourceWarning and on exceptions raised by finalizers. |
| [#49](https://github.com/RonaldFranklin/cerebro/pull/49) | ADR-0018 binds quotas to the authenticated credential, preserves unassigned historical rows and places validation and NullCurator before durable admission. Verify curation before approval. |
| [#50](https://github.com/RonaldFranklin/cerebro/pull/50) | Default to 20/hour and 10 pending. Set `proposals_per_hour` / `proposals_pending` in TOML or `CEREBRO_PROPOSALS_PER_HOUR` / `CEREBRO_PROPOSALS_PENDING` in the environment. Sample UTC under the writer lock and count the interval (now − 1h, now]. Terminal admissions count toward the hourly budget. Preserve recognized v1 schemas, including analyzed databases, and sealed payloads. |
| [#51](https://github.com/RonaldFranklin/cerebro/pull/51) | Run intake and amendment under the writer lock. Admit only scrubbed rejections or reviewable candidates. Return identical outcomes for hidden and missing references. Owner body edits use bounded stdin, revision checks and revalidation. Preserve attribution across edits. A manual rejection scrubs current submitted text and retains reviewed structured link endpoints for audit. |
| [#52](https://github.com/RonaldFranklin/cerebro/pull/52) | Enforce the closed wire schema and UTF-8 limits above, derive identity from the token, authorize discovery and dispatch by scope, and expose a safe validation result. A full read with both scopes returns a hash of the visible projection. A filtered view can therefore conflict safely on apply. |
| [#53](https://github.com/RonaldFranklin/cerebro/pull/53) | Isolate per-token pending and hourly refusals in a single SDK workflow. Check no database commit on refusal with SQLite data_version, read the exact commit from git and give each real client its own propose-only token for manual testing. |

### Security checkpoint for the owner

- Review the path from authenticated token id to atomic admission, test coverage for distinct tokens with a
  shared name, concurrency and migration of backed-up state. Legacy rows lack attribution and are excluded
  from token budgets. The accepted token-row integrity risk remains documented in [architecture](../architecture.md#accepted-risks-owner-decision-2026-11-25).
- Review the first durable write and the scrubbed rejection for sensitive or invalid content. Historical
  backups may still contain sealed payloads from earlier releases. Previously valid proposals can already be
  present in sealed backups when the owner later rejects them.
- The Phase 3 checkpoint awaits only the owner's real-client tests. The Phase 4 SDK acceptance is complete.
  The owner runs the [manual proposal checks](../agent-integration.md#3-propose-in-both-clients-and-approve-locally)
  in Claude Code and Codex and reviews the Phase 4 security checkpoint.
