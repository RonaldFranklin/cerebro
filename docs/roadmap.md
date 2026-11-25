# Roadmap

Work one phase at a time. A phase is done only when all its acceptance criteria are met and this file and the
"Current status" section of `AGENTS.md` are updated.

Status legend: ⬜ not started · 🟨 in progress · ✅ done

---

## ✅ Phase 0 — Bootstrap

Set up the repository so that quality rules are enforced from the first line of code.

- `backend/` with `uv`, `pyproject.toml`, `src/cerebro/{domain,application,adapters,bootstrap}` packages.
- Choose and pin the Python version and dev tools (check latest stable releases; record them in
  `docs/architecture.md` §7 after `uv.lock` is generated). Node is **not** chosen here: only the policy
  (Active LTS, [ADR-0013](adr/0013-stack-and-dependency-policy.md)) is fixed; the exact version comes in Phase 5.
- pytest, ruff (lint + format), mypy `--strict`, import-linter with the contracts from `docs/architecture.md` §1.
- `.editorconfig`, `.gitignore`, `.env.example`.
- GitHub Actions CI running all checks on push/PR.

**Acceptance:** a trivial test passes; CI is green; adding `import yaml` in `cerebro.domain` makes
import-linter fail.

*Done 2026-11-24.* The `import yaml` criterion is checked continuously by
`backend/tests/architecture/test_import_contracts.py`. Built-in import-linter contracts cannot express
"standard library only", so a small custom contract type lives in `backend/devtools/import_contracts.py`.

## ✅ Phase 1 — Domain and application core

Everything in `docs/domain-model.md` implemented in pure Python, plus the use cases with in-memory fakes.

Delivery plan, pull request status and design decisions: [plans/phase-1.md](plans/phase-1.md).

- Value objects and enums: `NoteId`, `Priority`, `Sensitivity`, `Status`, `NoteType`, `Note`, `Proposal`,
  `Decision`, `AccessCeiling`.
- Rules: field validation, budgets, link extraction, secret scanner, visibility by ceiling,
  superseded handling.
- Ports as `Protocol`s; in-memory fakes; contract test suites for each port.
- Use cases: `GetOverview`, `SearchNotes` (fake index), `ReadNote` (incl. section), `GetNeighbors`, `GetGraph`,
  `SubmitProposal`, `ValidateProposal`, `CurateProposal` (with `NullCurator`), `ReviewProposal`, `ApplyProposal`,
  `OwnerEditNote`, `RebuildIndex`, `HealthReport`, `IssueAgentToken`, `RevokeAgentToken`, and `AuthenticateToken`
  (turns a presented token secret plus the listener ceiling into an `AccessContext`; added because every inbound
  adapter needs it).
- Frontmatter is modeled as plain data in the domain; parsing YAML is **not** part of this phase.

**Acceptance:** high coverage on domain/application (target ≥ 90%); zero third-party imports in core;
all use cases runnable against fakes in tests.

*Done 2026-11-25.* Status of each criterion: coverage of domain + application is 100 % (gate ≥ 90 % in CI); the
`stdlib_only` import contract keeps `domain` and `application` free of third-party imports; every use case has
tests against the in-memory fakes, including denial and no-leak tests. Delivery record:
[plans/phase-1.md](plans/phase-1.md).

**Pending before Phase 3:** the human security checkpoint (plan section 5) over the secret scanner, the access
ceiling and token handling has not happened yet. Nothing in Phase 1 runs exposed.

## ⬜ Phase 2 — Outbound adapters

- `filesystem` NoteRepository (PyYAML frontmatter, content repo layout). Load with a resolver that keeps
  YAML 1.1 implicit booleans/numbers (`no`, `on`, `2026`, `0x1f`) as strings where the domain expects text, and
  dump such scalars quoted; contract-test it. Checks that `id` equals the file name.
- `sqlite` SearchIndex (FTS5) + GraphIndex + ProposalStore + TokenStore.
- `git` VersionControl (`push`) via subprocess; the filesystem `NoteRepository.apply` records the single commit with
  trailers, all-or-nothing. Its contract test must also read the commit back (`git log`) and assert the trailers,
  which the shared contract cannot observe.
- `system` adapters: UTC `Clock` and an `IdGenerator` whose `new_token_secret` is `secrets.token_urlsafe(32)`
  (CSPRNG, >= 256 bits; the in-memory fake is deterministic and must never be used outside tests).
- `crypto` Fernet Cipher; encrypted file format for restricted notes.
- Each adapter passes the same contract tests as its fake.
- Do not persist rejected raw payloads: a proposal rejected by validation keeps its findings (codes, fields,
  sizes), never the raw submitted text (it may contain the secret the scanner just caught).

**Acceptance:** `RebuildIndex` over a sample content repo (fixtures, including an encrypted note) produces a
correct index; contract tests green for fakes and real adapters.

## ⬜ Phase 3 — CLI and read-only MCP

- CLI: `init-content`, `reindex`, `search`, `read`, `health`, `token issue|list|revoke`, `inbox list|approve|reject`.
- MCP server with the read tools from [ADR-0014](adr/0014-mcp-tool-surface.md) over streamable HTTP
  (and stdio for local development), token auth, listener ceilings.
- `docs/agent-integration.md` verified against real Claude Code and Codex configs.
- Size limits in every inbound adapter (request body, query, proposal fields), enforced before any use case runs.
- `ListAgentTokens` use case (owner only; never returns secrets or hashes) for `token list`.
- MCP `cerebro_neighbors` caps `depth` at 2.

**Acceptance:** Claude Code and Codex in WSL (dev) can call `cerebro_overview`, `cerebro_search`,
`cerebro_read`, `cerebro_neighbors` against a dev instance; a token with ceiling `internal` never sees
restricted notes.

## ⬜ Phase 4 — Inbox

- `cerebro_propose` MCP tool.
- Full deterministic validation pipeline (schema, budgets, secrets, duplicates).
- Manual approval through CLI using `NullCurator` (owner writes/edits the final note).

**Acceptance:** an agent proposal flows received → validated → approved → applied → git commit with trailers;
a proposal containing a fake private key is blocked.

## ⬜ Phase 5 — HTTP API and web UI

- Choose the Node version per [ADR-0013](adr/0013-stack-and-dependency-policy.md) (Active LTS at phase start),
  pin it (`package.json` `engines`, `.nvmrc`, CI) and record it with the frontend versions in
  `docs/architecture.md` §7.
- FastAPI routers for read, graph, inbox and owner edit; single-user login (scrypt hash, secure session cookie).
- Web UI: note reader (browser-translatable), search, graph view (Sigma.js), local graph, filters,
  inbox review with diffs, owner editor (CodeMirror 6).

**Acceptance:** the owner can browse, read, search, explore the graph, approve proposals and edit notes from
the browser.

## ⬜ Phase 6 — Deploy (hml, prod)

- Dockerfile(s), `deploy/compose.yaml` with `cerebro-hml` and `cerebro-prod` projects, healthchecks,
  restart policy, log rotation, separate volumes.
- Same image promoted from hml to prod (`cerebro-backend:git-<sha>`).
- `tailscale serve` HTTPS in front of the private listener.
- Backups: content repo pushed to the private remote; state dir file backup; Fernet key escrow documented.
- Scheduled jobs never run between 00:00 and 01:00 local time (server shuts down daily at 01:00).

**Acceptance:** hml and prod running on the server; Claude Code and Codex on the workstation use prod via
Tailscale; restore of content + state tested on hml.

## ⬜ Phase 7 — Curator

- `Curator` adapters for Anthropic and OpenAI (provider chosen by configuration).
- Two-tier model: small model for triage, larger model for merge/create decisions.
- Normalization to English, frontmatter completion, link suggestions, duplicate merging.
- Autonomy policy (configurable): manual → auto-approve low risk → per-agent trust.

**Acceptance:** proposals are curated automatically and shown to the owner as decisions with diffs; cost per
curation logged.

## ⬜ Phase 8 — Later

- Public listener behind Cloudflare Tunnel + Cloudflare Access (service tokens), ceiling `internal`.
- Maintenance suggestions by the curator (orphans, overdue reviews, hub suggestions, splitting large notes).
- "Agent path" view in the graph (which notes an agent read in a session).
- Semantic search (embeddings) as an additional `SearchIndex` adapter, if FTS proves insufficient.
