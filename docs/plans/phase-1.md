# Phase 1 plan — Domain and application core

Approved by the owner on 2026-11-24.
Scope and acceptance criteria: [roadmap](../roadmap.md#-phase-1--domain-and-application-core).
This file records **how** the phase is delivered: one pull request per step, in order, plus the cross-cutting
design decisions every step follows. Update the status column when a pull request is opened or merged.

Status legend: ⬜ not started · 🟦 PR open · ✅ merged

## 1. Pull requests

| # | Branch | Content | Owner adjustments | Status |
|---|---|---|---|---|
| 1 | `phase-1/value-objects` | `NoteId`, `Priority`, `Sensitivity`, `Status`, `Area`, `NoteType`, `Vocabulary` (built-in `core`/`hub`), `Note`, domain errors; pytest-cov gate (≥ 90 %) | — | ✅ #3 |
| 2 | `phase-1/validation-and-budgets` | `parse_note` / `note_to_raw`, `validate_note`, `ValidationReport` + severity policy, word count, per-note and combined P0 budgets | D (combined budget takes per-note word counts, `Mapping[NoteId, int]`; `NoteRef` arrives in PR 3) | ✅ #4 |
| 3 | `phase-1/links-and-sections` | wikilink extraction (`[[id]]`, `[[id#Section]]`) plus `hub` / `related` / `superseded_by`; section extraction for `ReadNote`; cross-note rules: broken links (warning for owner edits, error for proposals), missing hub, `superseded_by` cycles, **hub chains acyclic and ≤ 3 hubs deep** | A (hub chain rules, [ADR-0015](../adr/0015-hub-membership-computed-at-read-time.md)), D | ✅ #7 |
| 4 | `phase-1/secret-scanner` | private keys, common token formats, `password=`-style assignments, high-entropy strings; scans **frontmatter (title, summary, …) and body**, for proposals **and** owner edits; negative tests (commit hashes, UUIDs) | D | ✅ #8 |
| 5 | `phase-1/access` | `AccessCeiling`, `effective_ceiling(listener, token)` (no defaults), visibility filter, `include_history`, `AgentToken`; `AccessContext(principal, ceiling, scopes)` with `principal = Agent(name) \| Owner` | C | ✅ #9 |
| 6 | `phase-1/proposals` | `Proposal`, `ProposalKind`, `ProposalStatus` and lifecycle transitions (invalid transition raises), `Decision` (reject, merge_into, create, needs_owner), `CommitMessage` with trailers; note **content hash**, `base_hash` on update proposals; update proposals carry the full body only (patch format: future ADR) | B | ✅ #10 |
| 7 | `phase-1/ports-and-fakes` | every port as a `Protocol`, in-memory fakes in `tests/fakes`, contract test suites in `tests/contract` parametrized by adapter factory (reused in Phase 2) | — | ✅ #11 |
| 8 | `phase-1/read-use-cases` | `GetOverview`, `SearchNotes`, `ReadNote`, `GetNeighbors`, `GetGraph`; hub reads build the member list at read time, filtered by ceiling | A, C | ✅ #12 |
| 9 | `phase-1/write-use-cases` | `SubmitProposal`, `ValidateProposal`, `CurateProposal` (with `NullCurator` / fake), `ReviewProposal`, `ApplyProposal`, `OwnerEditNote`; optimistic concurrency on apply and owner edit; authorization in the use cases | B, C, D | ✅ #13 |
| 10 | `phase-1/maintenance-use-cases` | `RebuildIndex`, `HealthReport`, `IssueAgentToken`, `RevokeAgentToken`, `AuthenticateToken`; closes the phase (roadmap incl. the added use cases, `AGENTS.md`, coverage check) | C | ✅ #14 |

**Phase 1 is complete** once PR 10 is merged. The human security checkpoint of section 5 (scanner, access
ceiling, tokens) is **still pending** and gates Phase 3.

Related docs pull request: [ADR-0015](../adr/0015-hub-membership-computed-at-read-time.md) (#2, merged) — hub
membership computed at read time.

## 2. Cross-cutting design decisions

1. **Exceptions vs. reports.**
   - Constructing a value that breaks an invariant raises a `DomainError` subclass (`InvalidValueError(field)`);
     an invalid value never exists.
   - Content validation never raises: rules return findings, and a **severity policy per write path**
     (`PROPOSAL_POLICY`, `OWNER_EDIT_POLICY`) turns them into a `ValidationReport` of `error` / `warning`
     violations with stable codes. All problems are reported at once.
   - Expected use-case outcomes are typed application exceptions (`NoteNotFound`, `PermissionDenied`,
     `InvalidTransition`), mapped to HTTP / MCP / CLI errors in one place per inbound adapter. A note above the
     caller's ceiling raises the same `NoteNotFound` (no existence leak). No generic `Result` type.
2. **Typed frontmatter.** `Note` is a frozen dataclass with typed fields and a `body`; no loose dicts in the
   domain. `parse_note(raw, body) -> Note | ValidationReport` is the only entry from raw frontmatter (what the
   YAML loader returns) and reports every structural problem; `note_to_raw(note)` writes plain data in a
   canonical field order for stable diffs. Unknown keys are errors. `NoteType` and `Area` are validated strings
   checked against the `Vocabulary`, not enums. Encryption stays in the filesystem adapter (via the `Cipher`
   port); the core only sees plaintext notes.
3. **Use cases.** One class per use case, ports injected in `__init__`, a single
   `execute(request) -> response`. Requests and responses are frozen dataclasses in `application/dto.py`;
   requests carry domain types, built by the inbound adapter. Responses are dedicated DTOs (`NoteSummary`,
   `NoteView`, `GraphView`), never the `Note` entity. Every use case receives an `AccessContext`. Time and ids
   come from the `Clock` and `IdGenerator` ports (proposal ids: `uuid.uuid7()` in the system adapter).
4. **Immutability.** `@dataclass(frozen=True, slots=True, kw_only=True)` across the domain and DTOs; `tuple` /
   `frozenset`, never `list`. Changes produce new instances (e.g. `proposal.mark_validated(...)`); ports
   persist them.
5. **Cross-note validation snapshot.** Rules that need other notes are pure domain functions over a snapshot
   (`Mapping[NoteId, NoteRef]`) built by the application from the ports. `NoteRef` is a light view of each
   note's metadata — **no body**; its exact fields are defined in PR 3. Duplicate detection (FTS similarity)
   lives in `ValidateProposal`, through the `SearchIndex` port.
6. **Word count.** Whitespace-separated tokens of the body (frontmatter excluded; Markdown syntax and code
   blocks count). The combined P0 budget counts only current P0 notes.

## 3. Owner adjustments

| | Adjustment | Pull requests |
|---|---|---|
| **A** | Hubs store only their introduction (≤ 800 words); members are computed at read time from the `hub` field and filtered by ceiling ([ADR-0015](../adr/0015-hub-membership-computed-at-read-time.md)). A hub may point to a parent hub; hub chains are acyclic and at most 3 hubs deep. | ADR in #2; budget in 2; chain rules in 3; member list in 8 |
| **B** | Optimistic concurrency: every note exposes a content hash; update proposals carry `base_hash`; `ApplyProposal` refuses if the note changed (proposal goes back to `needs_owner` with the conflict flagged); `OwnerEditNote` uses the same mechanism. | 6, 9 |
| **C** | `AccessContext(principal, ceiling, scopes)` with `principal = Agent(name) \| Owner`. Authorization is checked in the use cases: `SubmitProposal` requires scope `propose`; `OwnerEditNote`, `ReviewProposal` and maintenance require `Owner`; `source.agent` always comes from the context, never from the payload. | 5, 8, 9, 10 |
| **D** | The secret scanner covers frontmatter (title, summary, …) and body, for owner edits too. The validation snapshot uses a light `NoteRef` without body. | 2, 3, 4, 9 |

## 4. Other scope decisions

- Phase 1 also includes `CurateProposal` (with `NullCurator` / a fake), `GetGraph` and
  `IssueAgentToken` / `RevokeAgentToken`; the roadmap is updated in PR 10.
- Update proposals carry the full new body; a patch format needs a future ADR.
- pytest-cov is a dev dependency (coverage gate ≥ 90 % on domain + application).

## 5. Working mode (autonomous execution, authorized 2026-11-25)

Applies to PRs 3–10 and to future sessions continuing this phase.

- **Orchestrator.** The main session only keeps summaries and does not read whole files unnecessarily. For each PR:
  a fresh implementation subagent (Sonnet) receives that PR's plan excerpt; a separate review subagent
  (Sonnet; **Opus for the secret scanner and access PRs**) reviews it.
- **Merge authority.** The agent merges every PR 3–10, including those that change `domain-model.md` and the
  security ones (scanner, access), once CI is green and the review is resolved. Rationale: nothing in Phase 1 runs
  exposed. A **human security review happens at a checkpoint at the end of the phase, before Phase 3.**
- **Stop only if** a new dependency is needed, the approved plan or an ADR must be departed from, or work is blocked.
- **Minor doubts:** decide, and list them in the PR description under "Decisions for review".
- **Final report** to the owner: merged PRs, tests and coverage, all "Decisions for review", and a security section
  (scanner, access ceiling, tokens).

## 6. Decisions confirmed for PR 2 (owner delegated to the recommendations, 2026-11-25)

1. Type decides priority: `core` ⇒ P0, `hub` ⇒ P1, any other type ⇒ P2/P3.
2. Core notes may not set `hub`.
3. Severity policy per write path (`PROPOSAL_POLICY`, `OWNER_EDIT_POLICY`).
4. Strict frontmatter: unknown keys are errors, `null` is missing, timestamps rejected, non-mapping reported.
5. Title single line ≤ 120 chars after trimming; word count = whitespace tokens; hub intro > 800 words is an error;
   the core budget counts current P0 notes only.
