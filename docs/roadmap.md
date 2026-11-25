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

**Security checkpoint: done 2026-11-25.** The owner approved the Phase 1 security review, conditioned on a batch of
follow-up pull requests (#15-#22, merged): links may not point to higher sensitivity (and are redacted on read),
optimistic concurrency on `ProposalStore`, no raw values in errors and a redacted `TokenHash`, an import contract
keeping inbound adapters away from ports and outbound adapters, token clock-skew check, `curator/` names, structured
scanner findings, and [ADR-0016](adr/0016-curator-never-sees-restricted.md) (the curator never receives restricted
notes). Details: [plans/phase-1.md](plans/phase-1.md) section 7.

## ✅ Phase 2 — Outbound adapters

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

*Done 2026-11-25.* Delivery record: [plans/phase-2.md](plans/phase-2.md). Beyond the list above, Phase 2 added a minimal
composition root (`bootstrap`: `Settings`, `build_container`, a single writer lock, start-up reindex when the index
revision differs from the repository `HEAD`) and the acceptance test `tests/integration/test_rebuild_acceptance.py`.

## 🟨 Phase 3 — CLI and read-only MCP

Delivery plan: [phase-3.md](plans/phase-3.md).

- CLI: `init-content`, `reindex`, `search`, `read`, `health`, `token issue|list|revoke`, `inbox list|approve|reject`.
- MCP server with the read tools from [ADR-0014](adr/0014-mcp-tool-surface.md) over streamable HTTP
  (and stdio for local development), token auth, listener ceilings.
- `docs/agent-integration.md` provides exact manual test commands for Claude Code and Codex with isolated configuration.
- Size limits in every inbound adapter (request body, query, proposal fields), enforced before any use case runs.
- `ListAgentTokens` use case (owner only; never returns secrets or hashes) for `token list`.
- MCP `cerebro_neighbors` caps `depth` at 2.

**Acceptance:** An SDK test client calls `cerebro_overview`, `cerebro_search`,
`cerebro_read`, `cerebro_neighbors` against a dev instance containing a restricted fixture. A token with ceiling `internal` sees only
permitted fixtures. The owner runs the real-client checks and reviews the security checkpoint.

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
