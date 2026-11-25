# Phase 2 plan — Outbound adapters

Approved by the owner on 2026-11-25 (with adjustments 1-4 below).
Scope and acceptance criteria: [roadmap](../roadmap.md#-phase-2--outbound-adapters).
Same working mode as [Phase 1](phase-1.md) section 5: one branch and one pull request per step, a fresh
implementation sub-agent (Sonnet), a fresh review sub-agent (Sonnet; **Opus** for crypto, tokens, access
ceiling and filesystem paths), merge by the agent once CI is green and the review is resolved (owner authorization of
2026-11-25, including the two new runtime dependencies below). If the classifier blocks a merge, ask the owner at the
prompt and carry on afterwards.

Status legend: ⬜ not started · 🟦 PR open · ✅ merged

## 1. Pull requests

| # | Branch | Content | Dependency | Status |
|---|---|---|---|---|
| 1 | `phase-2/system-adapters` | `UtcClock`, `SystemIdGenerator` (`uuid.uuid7()`, `secrets.token_urlsafe(32)`), stdlib only. Creates `adapters/outbound/` and `tests/integration/`; contract tests parametrized by adapter factory (fake and real) | — | ✅ #25 |
| 2 | `phase-2/index-revision` | Port change: `NoteRepository.head()` (content revision) and `SearchIndex`/`GraphIndex` record and expose the revision they were built from; `RebuildIndex` records it; `HealthReport` flags a stale index; fakes and contract tests | — | ✅ #26 |
| 3 | `phase-2/crypto` | `FernetCipher` over `MultiFernet` (key list: the first encrypts, all decrypt); key loading refuses a file or directory more open than 0600/0700; encrypted restricted-note file format (ADR-0008); `DecryptionError` on tampering | `cryptography` | ✅ #27 |
| 4 | `phase-2/sqlite-state` | `state.sqlite`: `ProposalStore` (content and decision always encrypted with the `Cipher`; metadata in clear; compare-and-swap on `revision`) and `TokenStore`. Rejected proposals keep findings only, never the raw payload | — | ✅ #30 |
| 5 | `phase-2/sqlite-index` | `index.sqlite`: `SearchIndex` (FTS5, tokenizer `porter unicode61`) and `GraphIndex`; stores the revision it was built from; ceiling applied in SQL; user text never reaches `MATCH` unescaped | — | ✅ #29 |
| 6 | `phase-2/git` | `VersionControl.push` and the git subprocess helper (no shell, fixed environment, hooks off, `--` before paths, message from stdin with trailers, timeouts) | — | ✅ #28 |
| 7 | `phase-2/filesystem-repo` | `NoteRepository` over files: PyYAML with a restricted loader, `id` equals file name, atomic writes, all-or-nothing `apply` with one commit and trailers, single-writer lock, encrypted restricted notes, `head()`. Contract test reads the commit back | `PyYAML` | ✅ #31 |
| 8 | `phase-2/rebuild-acceptance` | Minimal `bootstrap/container.py`, which holds ONE writer lock around read-head/`apply`/advance-revision (serialized writers, required by the index revision protocol of PR 2); at start-up it compares the index revision with the repository `HEAD` and reindexes when they differ; fixture content repo with an encrypted note; end-to-end `RebuildIndex` test (acceptance) | — | ✅ #32 |
| 9 | `docs/phase-2-close` | Roadmap, `AGENTS.md` section 3, this file, `docs/architecture.md` dependency table | — | 🟦 this PR |

Order: 1 and 2 first; 3 before 4 and 7; 5 and 6 are independent; 7 needs 3 and 6; 8 needs everything.

## 2. Owner adjustments

| | Adjustment | Pull requests |
|---|---|---|
| **1** | `state.sqlite` is backed up, and a proposal may hold restricted content: the `ProposalStore` always encrypts proposal content (title, body, rationale, suggested fields) and the decision with the `Cipher`; id, status, revision, agent and dates stay in clear for queries. Hence crypto comes before sqlite-state. | 3, 4 |
| **2** | `index.sqlite` stores the commit (`HEAD`) it was built from. At container start-up and in `HealthReport`, a different `HEAD` means a stale index; the container reindexes. | 2, 8 |
| **3** | `Cipher` uses `MultiFernet` from the start: a list of keys, the first encrypts, all decrypt. Key loading refuses a file with permissions more open than 0600. | 3 |
| **4** | FTS5 uses the `porter unicode61` tokenizer. | 5 |

## 3. Design decisions

1. Two SQLite files ([ADR-0002](../adr/0002-markdown-git-source-of-truth.md)): disposable `index.sqlite`, backed-up
   `state.sqlite`. One connection per operation; WAL; schema version in `PRAGMA user_version`; files 0600 in a 0700
   directory (the index holds decrypted text and never goes to a backup).
2. `NoteRepository.apply` only writes and commits; the use case updates the indexes afterwards. If that fails,
   `RebuildIndex` recovers, and the stale-revision check detects it.
3. A restricted note file is encrypted whole (title and summary included) behind a plaintext header
   (`id`, `sensitivity`, `encryption`), per [ADR-0008](../adr/0008-encryption-of-restricted-notes.md).
4. Filesystem safety: only ids that pass the slug rule become paths; symlinks refused; resolved path must stay inside
   the root; file and YAML size capped before loading; YAML anchors, tags and duplicate keys rejected.
5. One exclusive file lock around `apply` (single writer).
6. Configuration is explicit parameters of the container; environment and config files arrive with the CLI in Phase 3.
7. Roadmap item: rejected proposals do not persist raw payloads (`ValidateProposal` scrubs content before saving).
8. Contract tests from Phase 1 are reused as they are; each adapter PR adds its factory and the behaviours the shared
   contract cannot see (commit trailers, file permissions, ciphertext at rest).

## 4. Dependencies (owner-authorized 2026-11-25)

`PyYAML` (adapter only, `safe_load` through a restricted loader) and `cryptography` (adapter only, Fernet). Both
already named in [ADR-0013](../adr/0013-stack-and-dependency-policy.md); pinned exactly to the newest stable release
published at least 7 days earlier, and recorded in `docs/architecture.md` section 7 in the same PR.

## 5. Delivery notes

- Actual merge order: 1 (#25), 2 (#26), 3 crypto (#27), 6 git (#28), 5 index (#29), 4 state (#30), 7 filesystem (#31),
  8 acceptance (#32). Every security-relevant PR went through at least one fresh Opus review; the findings were fixed
  before merge (details in each PR's "Decisions for review" and review sections).
- Dependencies added: `cryptography==50.0.2`, `pyyaml==6.0.3` (owner-authorized) and the dev-only
  `types-pyyaml==6.0.12.20261113` (needed by `mypy --strict`; not covered by the original authorization, flagged to the owner).
- Beyond the plan: the FTS5 index keeps one table per sensitivity tier (bm25 statistics would otherwise leak hidden
  terms), `GitRepo` gained rollback-by-path and a hardened environment, the container holds a single writer lock, and
  an import contract keeps inbound adapters away from `cerebro.bootstrap`.
