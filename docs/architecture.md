# Architecture

Cérebro follows a **hexagonal (ports and adapters)** architecture. The core (domain + application) knows nothing
about YAML, SQLite, git, HTTP, MCP or any LLM provider. See [ADR-0001](adr/0001-hexagonal-architecture.md).

## 1. Layers

```
                   INBOUND ADAPTERS (driving)
     MCP server · HTTP API (FastAPI) · CLI · Curator worker · Scheduler
                               │ calls use cases
                               ▼
      ┌────────────────────────────────────────────────────────┐
      │ APPLICATION — use cases + ports (typing.Protocol)       │
      │                                                        │
      │   ┌────────────────────────────────────────────────┐   │
      │   │ DOMAIN — pure Python, standard library only    │   │
      │   │ Note · NoteId · Link · Priority · Sensitivity  │   │
      │   │ Status · Proposal · Decision · AccessCeiling   │   │
      │   │ validation rules · budgets · link extraction   │   │
      │   └────────────────────────────────────────────────┘   │
      └────────────────────────────────────────────────────────┘
                               │ through ports
                               ▼
                   OUTBOUND ADAPTERS (driven)
   Markdown files · SQLite FTS5 index · git CLI · Fernet cipher
   LLM providers (Claude / OpenAI) · token store · clock · id generator
```

### Dependency rule

| Package | May import |
|---|---|
| `cerebro.domain` | Python standard library only |
| `cerebro.application` | `cerebro.domain`, standard library |
| `cerebro.adapters.*` | `cerebro.application`, `cerebro.domain`, third-party libraries |
| `cerebro.bootstrap` | everything (composition root) |

Enforced with `import-linter` contracts in CI. The "standard library only" rule for `cerebro.domain` and
`cerebro.application` is enforced by a contract of the custom `stdlib_only` type, defined in
`backend/devtools/import_contracts.py` (static imports only; dynamic imports are not detected).

## 2. Backend package layout (target)

```
backend/
├── pyproject.toml
├── uv.lock
├── src/cerebro/
│   ├── domain/
│   │   ├── note.py            # Note (typed frontmatter fields + body), NoteId
│   │   ├── links.py           # wikilink extraction [[id]] / [[id#Section]]
│   │   ├── enums.py           # Priority, Sensitivity, Status
│   │   ├── vocabulary.py      # Area, NoteType, Vocabulary (controlled lists from schema/*.yaml)
│   │   ├── slug.py            # kebab-case rule shared by ids, areas and types
│   │   ├── access.py          # AccessCeiling, visibility rules
│   │   ├── budgets.py         # size/word/summary limits
│   │   ├── proposal.py        # Proposal, ProposalKind, lifecycle, Decision
│   │   ├── secrets_scan.py    # regex patterns that block secret-looking content
│   │   ├── validation.py      # note + proposal validation, returns violations
│   │   └── errors.py
│   ├── application/
│   │   ├── ports/             # Protocols (see section 3)
│   │   ├── use_cases/         # one module per use case (see section 4)
│   │   └── dto.py             # plain dataclasses crossing the boundary
│   ├── adapters/
│   │   ├── inbound/
│   │   │   ├── mcp/           # MCP server (streamable HTTP + stdio for dev)
│   │   │   ├── http/          # FastAPI routers, auth, schemas (Pydantic lives here)
│   │   │   └── cli/
│   │   └── outbound/
│   │       ├── filesystem/    # Markdown + YAML frontmatter repository
│   │       ├── sqlite/        # FTS5 search index, graph tables, proposal store, token store
│   │       ├── git/           # git via subprocess
│   │       ├── crypto/        # Fernet cipher
│   │       └── llm/           # curator adapters (anthropic, openai), null curator
│   └── bootstrap/
│       ├── settings.py        # configuration from env/file
│       └── container.py       # wires adapters into use cases (composition root)
└── tests/
    ├── unit/                  # domain + application with in-memory fakes
    ├── contract/              # shared port contracts run against fakes AND real adapters
    ├── integration/           # real adapters (tmp dirs, sqlite, git)
    └── fakes/
```

## 3. Ports

| Port | Responsibility | Adapter(s) |
|---|---|---|
| `NoteRepository` | load/save/list notes in the content repo | filesystem; in-memory fake |
| `SearchIndex` | full-text search with filters, returns summaries only | SQLite FTS5 |
| `GraphIndex` | backlinks, neighbors up to N hops, orphans, broken links | SQLite tables |
| `ProposalStore` | persist proposals and decisions (audit) | SQLite |
| `VersionControl` | commit with trailers, push to backup remote | git CLI |
| `Cipher` | encrypt/decrypt restricted note payloads | Fernet |
| `Curator` | suggest a decision for a validated proposal | Anthropic, OpenAI, `NullCurator` |
| `TokenStore` | agent tokens (hashed), scopes and ceilings | SQLite |
| `Clock`, `IdGenerator` | time and ids, for deterministic tests | system / fixed fakes |

Ports are `typing.Protocol` classes in `cerebro.application.ports`. Each one has an in-memory fake in
`tests/fakes` and a contract test suite in `tests/contract`.

## 4. Use cases

**Read**
- `GetOverview` — P0 core + list of areas and hubs (first call of every agent).
- `SearchNotes` — query + filters (area, type, priority, status) → ids, titles, summaries.
- `ReadNote` — full note or a single section; enforces the access ceiling.
- `GetNeighbors` — incoming/outgoing links up to N hops (also feeds the UI graph).
- `GetGraph` — full graph (nodes + edges + attributes) for the UI, filtered by ceiling.

**Write**
- `SubmitProposal` — store an incoming proposal in the Inbox (agents only have this).
- `ValidateProposal` — deterministic checks: schema, budgets, secret scan, duplicates.
- `CurateProposal` — ask the `Curator` port for a decision (reject / merge / create / needs-human).
- `ReviewProposal` — owner approves, edits or rejects.
- `ApplyProposal` — turn an approved decision into note changes + commit.
- `OwnerEditNote` — owner edits directly (still validated, committed with owner as author).

**Maintenance**
- `RebuildIndex` — re-read the content repo, decrypt restricted notes in memory, rebuild the index.
- `HealthReport` — orphans, broken links, overdue `review_by`, budget violations.
- `IssueAgentToken` / `RevokeAgentToken`.

## 5. Runtime topology

Runtime is **server-only** ([ADR-0012](adr/0012-environments-and-runtime.md)). The owner's workstation runs
agents and a browser; nothing of Cérebro runs there in hml/prod.

```
Workstation                                    Server (docker compose: cerebro-<env>)
┌───────────────────┐                          ┌──────────────────────────────────────────┐
│ Claude Code       │                          │ backend container                         │
│ Codex             │── Tailscale (HTTPS) ────▶│   private listener  (ceiling: restricted) │
│ Browser (web UI)  │                          │   public listener   (ceiling: internal)   │
└───────────────────┘                          │ web (static, served by backend or proxy)  │
                                               │ curator worker (same image, other command)│
(future) cloud agents ── Cloudflare Tunnel ───▶│ cloudflared ──▶ public listener only      │
                         + Cloudflare Access   └──────────────────────────────────────────┘
                                                 volumes: content repo (git), state (sqlite),
                                                          secrets (Fernet key, admin hash)
```

- The **private listener** is exposed only through `tailscale serve` (HTTPS, tailnet only).
- The **public listener** is reachable only by the `cloudflared` container. Not enabled initially.
- The listener a request arrived on sets the maximum ceiling; the agent token can only lower it.
  See [ADR-0005](adr/0005-sensitivity-and-access-ceiling.md) and
  [ADR-0010](adr/0010-network-access.md).

### State on disk

| What | Where (configurable) | In git? | Backup |
|---|---|---|---|
| Notes | content repo working copy | yes (private repo) | push to private remote |
| Search/graph index | state dir, `index.sqlite` | no | no — rebuildable |
| Proposals, decisions, tokens | state dir, `state.sqlite` | no | yes (file backup) |
| Fernet key, admin password hash | secrets dir | no | owner keeps an offline copy of the key |

## 6. Frontend

- React + TypeScript + Vite. Data fetching through a small `api/` layer (the UI's port); components never call
  `fetch` directly. TanStack Query for caching.
- **Graph:** Sigma.js (WebGL) + graphology; ForceAtlas2 layout in a web worker. Node size by priority,
  color by area or type (toggle), filters by priority/status/area/sensitivity, local graph (N hops) on click.
- **Reader:** notes rendered as real HTML with `lang` set so browser translation works. Code blocks, inline
  code, paths, commands, note ids and links carry `translate="no"`.
- **Inbox review:** list proposals, see validation results and curator decision as a diff, approve/edit/reject.
- **Editor (owner):** CodeMirror 6 with `[[link]]` autocomplete; saves go through `OwnerEditNote`.
- English only; no i18n library ([ADR-0009](adr/0009-english-only.md)).

## 7. Dependency policy and stack

Full policy in [ADR-0013](adr/0013-stack-and-dependency-policy.md). Summary:

| Area | Choice | Notes |
|---|---|---|
| Python | 3.14 (newest minor GA for ≥ 12 months at bootstrap) | `uv` for env + lockfile |
| Core | standard library only | `dataclasses`, `enum`, `re`, `typing` |
| Index | `sqlite3` (stdlib) with FTS5 | |
| Frontmatter | PyYAML (`safe_load` / `safe_dump`) | adapter only |
| Git | `git` CLI via `subprocess` | no git library |
| Crypto | `cryptography` (Fernet) | adapter only |
| Password hashing | `hashlib.scrypt` (stdlib) | single admin user |
| HTTP | FastAPI + Uvicorn | adapter only |
| MCP | official `mcp` Python SDK | adapter only |
| CLI | Typer (or argparse if we want zero deps) | decided in Phase 3 |
| LLM | provider SDKs, one adapter each | optional, Phase 7 |
| Quality | pytest, ruff, mypy (strict), import-linter | custom `stdlib_only` contract type in `backend/devtools/` |
| Web | Node Active LTS (exact version chosen in Phase 5), pnpm, React, TypeScript, Vite, TanStack Query, Sigma.js, graphology, CodeMirror 6 | |

All versions are pinned exactly in lockfiles. Exact versions are the newest stable releases published at least
7 days earlier ([ADR-0013](adr/0013-stack-and-dependency-policy.md)), and recorded here once the lockfile is generated, so this table matches the lock.

### Pinned backend versions (Phase 0)

Source of truth: `backend/uv.lock`. Update this table in the same commit as any upgrade. Every version must
have been published at least 7 days earlier ([ADR-0013](adr/0013-stack-and-dependency-policy.md)).

| Tool | Version | Role |
|---|---|---|
| Python | 3.14 (`requires-python = "==3.14.*"`, `.python-version`) | runtime; 3.14.10 installed at bootstrap |
| uv | ≥ 0.13.9 (`tool.uv.required-version`; 0.13.9 in CI) | env, lockfile, runner |
| uv_build | 0.13.9 | build backend |
| pytest | 9.2.0 | tests |
| pytest-cov | 7.1.0 (pulls coverage 7.17.3) | coverage; `fail_under = 90` on `cerebro.domain` + `cerebro.application` |
| ruff | 0.17.5 | lint + format |
| mypy | 2.4.1 | type checking (`strict`) |
| import-linter | 2.16 | import contracts (pulls grimp 3.20) |

Runtime dependencies: none yet. Frontend versions (Node, pnpm, packages) are added in Phase 5.
