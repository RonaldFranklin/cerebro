# Architecture

Cérebro follows a **hexagonal (ports and adapters)** architecture. The core (domain + application) knows nothing
about YAML, SQLite, git, HTTP, MCP or any LLM provider. See [ADR-0001](adr/0001-hexagonal-architecture.md).

The write path below is the Phase 7 target. The [delivery plan](plans/phase-7.md) tracks its rollout.
[ADR-0020](adr/0020-direct-agent-writes.md) records the change from the Inbox shipped in Phases 4 and 5.

## 1. Layers

```
                   INBOUND ADAPTERS (driving)
     MCP server · HTTP API (FastAPI) · CLI · Scheduler
                               │ calls use cases
                               ▼
      ┌────────────────────────────────────────────────────────┐
      │ APPLICATION — use cases + ports (typing.Protocol)       │
      │                                                        │
      │   ┌────────────────────────────────────────────────┐   │
      │   │ DOMAIN — pure Python, standard library only    │   │
      │   │ Note · NoteId · Link · Priority · Sensitivity  │   │
      │   │ Status · AccessCeiling                         │   │
      │   │ validation rules · budgets · link extraction   │   │
      │   └────────────────────────────────────────────────┘   │
      └────────────────────────────────────────────────────────┘
                               │ through ports
                               ▼
                   OUTBOUND ADAPTERS (driven)
   Markdown files · SQLite FTS5 index · git CLI · Fernet cipher
   Token store · rate limits · clock · id generator
```

### Dependency rule

| Package | May import |
|---|---|
| `cerebro.domain` | Python standard library only |
| `cerebro.application` | `cerebro.domain`, standard library |
| `cerebro.adapters.*` | `cerebro.application`, `cerebro.domain`, third-party libraries |
| `cerebro.adapters.inbound` | use cases, DTOs and errors of `cerebro.application`, `cerebro.domain`, third-party libraries; **never** `cerebro.application.ports` nor `cerebro.adapters.outbound` |
| `cerebro.bootstrap` | everything (composition root); **never** imported by `cerebro.adapters.inbound` |

Inbound adapters talk only to use cases and DTOs; the concrete ports and outbound adapters are wired into the
use cases in `cerebro.bootstrap`. Inbound code never receives the `Container`, only individual use cases wired by
the entry point. Indirect imports (inbound -> use case -> ports) are allowed.

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
│   │   ├── budgets.py         # word count, per-note and combined P0 budgets
│   │   ├── secrets_scan.py    # regex patterns that block secret-looking content
│   │   ├── report.py          # Violation, ViolationCode, Severity, ValidationReport
│   │   ├── frontmatter.py     # parse_note (raw mapping -> Note | report), note_to_raw (canonical order)
│   │   ├── validation.py      # note validation, returns violations
│   │   └── errors.py
│   ├── application/
│   │   ├── ports/             # Protocols (see section 3)
│   │   ├── errors.py          # NoteNotFound, PermissionDenied, ...
│   │   ├── use_cases/         # one module per use case (see section 4)
│   │   └── dto.py             # plain dataclasses crossing the boundary
│   ├── adapters/
│   │   ├── inbound/
│   │   │   ├── mcp/           # MCP server (streamable HTTP + stdio for dev)
│   │   │   ├── http/          # FastAPI routers, auth, schemas (Pydantic lives here)
│   │   │   └── cli/
│   │   └── outbound/
│   │       ├── filesystem/    # Markdown + YAML frontmatter repository
│   │       ├── sqlite/        # FTS5 search index, graph tables, token store and write limits
│   │       ├── git/           # git via subprocess
│   │       └── crypto/        # Fernet cipher
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
| `NoteRepository` | `get`, `list_refs` (body-free `NoteRef` snapshot), `list_notes`, `head()` (the current content revision: the id the last `apply` returned, `None` when empty), and `apply(changes, message)`: write one or more notes and record **one** commit with the trailers, all-or-nothing; each `NoteChange` carries the `expected_hash` (`None` = must not exist) and a mismatch raises `ContentConflict` | filesystem; in-memory fake |
| `SearchIndex` | `upsert` / `remove` / `rebuild(notes, revision=)`, `built_from()` / `set_revision()` (the content revision the index reflects; `upsert`/`remove` never change it), `search(SearchQuery)` (ceiling mandatory; order: score, priority, id) and `find_similar` (duplicate detection); returns summaries only | SQLite FTS5 |
| `GraphIndex` | `rebuild(refs, revision=)`, `built_from()` / `set_revision()` (as `SearchIndex`), `graph`, `backlinks`, `neighbors(hops)`, `orphans`, `broken_links`; every query takes the ceiling, hidden notes are neither nodes nor broken links | SQLite tables |
| `VersionControl` | `push` to the backup remote. Commits are made by `NoteRepository.apply`, so a write and its commit cannot diverge | git CLI |
| `Cipher` | encrypt/decrypt restricted note payloads (used by the filesystem adapter only); `DecryptionError` on tampering | Fernet |
| `TokenStore` | `add`, `get`, `find_by_hash` (also returns revoked tokens), `save`, `all`; tokens are stored hashed | SQLite |
| `WriteAttemptStore` | Atomically reserve a rolling-hour slot for an authenticated token; a refusal leaves state unchanged | SQLite; in-memory fake |
| `ChangeHistory` | Return recent first-parent revisions, attribution and note versions for private owner history | Git/filesystem; in-memory fake |
| `Clock`, `IdGenerator` | `now()`; note ids, token ids and token secrets, for deterministic tests | system / fixed fakes |

Recent changes scans at most 32 first-parent revisions and shows up to 20 attributable note changes.
The application checks current and historical visibility before returning the actor, timestamp, note and
unified diff. Diff projections are bounded and indicate truncation. Git retains the complete history.

Ports know nothing about access: use cases apply the ceiling and raise `NoteNotFound` for hidden notes exactly as
for missing ones, while the two indexes filter by the mandatory ceiling argument. Application exceptions live in
`cerebro.application.errors`.

Ports are `typing.Protocol` classes in `cerebro.application.ports`. Each one has an in-memory fake in
`tests/fakes` and a contract test suite in `tests/contract`.

## 4. Use cases

**Read**
- `GetOverview` — P0 core + list of areas and hubs (first call of every agent).
- `SearchNotes` — query + filters (area, type, priority, status) → ids, titles, summaries.
- `ReadNote` — full note or a single section; enforces the access ceiling.
- `GetNeighbors` — incoming/outgoing links up to N hops (also feeds the UI graph).
- `GetGraph` — full graph (nodes + edges + attributes) for the UI, filtered by ceiling.

All read use cases take `execute(request, ctx)`, require the `read` scope (`PermissionDenied` otherwise, checked
first) and apply `ctx.ceiling` (`include_history` only adds `superseded`/`archived`). A hidden note, a missing note
and a hidden neighbors center all raise the same `NoteNotFound`; search results, hub member lists, neighbors and
the graph contain visible notes only and carry no totals, so nothing reveals a hidden note. `GetOverview` serves
current P0 notes in id order within the 1,500-word core budget (`core_truncated` when some do not fit) plus the
visible hubs and their areas. `ReadNote` accepts `id`, `id#Section` or `[[id#Section]]` (`SectionNotFound` for a
visible note without that heading) and, for a hub, returns the member list built at read time (ADR-0015). The
`hub`, `related` and `superseded_by` references of a note are dropped when their target is hidden or missing; hits
and graph nodes from the indexes are re-checked against the ceiling (defense in depth). Wikilinks inside a served
body (whole or one section) whose target the reader cannot see, or that does not exist, are replaced by the constant
`[[redacted link]]`; the write path additionally forbids links to a higher sensitivity (`link_to_higher_sensitivity`, see
`docs/domain-model.md`).

**Write**

- An authenticated agent with `write` calls `cerebro_write` to create or update a note. The application
  counts attempts per token and checks the candidate under the writer lock. It enforces the effective ceiling,
  frontmatter schema, budgets, link rules, secret scan and content-hash precondition before committing.
  Core and P0 notes, the overview's global rules, additionally need the `core` scope on the stored version
  and on the candidate ([ADR-0022](adr/0022-core-scope.md)).
  The repository records the agent from authentication. The MCP adapter returns a commit receipt or a
  bounded, redacted error with a correction the agent can make.
- `OwnerEditNote` keeps the owner's validated edit path. Both paths warn when raising a note to
  restricted leaves an earlier plaintext revision in git.
- `cerebro_write_guide` serves the versioned English writing instructions. See the
  [write decision](adr/0020-direct-agent-writes.md) and [domain contract](domain-model.md#7-agent-writes).

**Maintenance**
- `RebuildIndex` — re-read the content repo, decrypt restricted notes in memory, rebuild the index (owner only; also
  the recovery path when an index update failed after a commit).
- `HealthReport` — orphans, broken links, hub chains, overdue `review_by`, budget violations, secrets in existing
  notes (never echoed), stale indexes (owner only, restricted ceiling).
  An index is stale when its `built_from()` differs from the repository `head()` (`index_is_stale`; the
  `Container.start()` reindexes when it is stale, implemented in Phase 2 PR 8), or when its content
  differs note by note. `RebuildIndex` records `head()` (read before the notes); agent writes and
  `OwnerEditNote` advance the recorded revision after updating an index that was fresh before the commit, so a
  failed index update stays visible until `RebuildIndex` runs.
  **Single writer:** reading `head()`, `apply` and advancing the revision must run under ONE writer lock held by
  the composition root (ADR-0002: writes are serialized through the application). Implemented as `WriterLock` in
  `cerebro.bootstrap.container` (a thread lock plus `flock` on `<state_dir>/writer.lock`) wrapping agent writes,
  `OwnerEditNote` and `RebuildIndex`. Without it, a concurrent
  commit between those steps can make the conditional advance hide a failed index update. `rebuild(revision=)`
  must store the revision atomically with the rows.
- `IssueAgentToken` / `RevokeAgentToken` — owner only; only the SHA-256 hash is stored, the secret is shown once.
- `AuthenticateToken` — presented secret + listener ceiling -> `AccessContext` (used by every inbound adapter).

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
| Tokens, write limits and retained legacy Inbox state | state dir, `state.sqlite` | no | yes (file backup) |
| Fernet key, admin password hash | secrets dir | no | owner keeps an offline copy of the key |

Filesystem repository, operating notes (Phase 2):

- `list_notes`/`list_refs` read the working tree while `head()` reports `HEAD`: the container must keep the content
  tree clean (`Container` does not check this itself; start-up reindexing does not detect uncommitted edits). `apply` enforces it for writes (it refuses when anything is staged, or when a planned note is untracked,
  has uncommitted edits, or a new note's path is already known to git) and commits only the planned paths.
- `get` takes no lock (each file is replaced atomically); whole-tree listings take a shared lock.
- Replacing an encrypted restricted file by a plaintext file declaring `internal` or `public` cannot be detected by
  the adapter (the file is a valid plaintext note). Protect the working tree and review commits.
- Directory components are walked with `openat`-style calls (no symlink swap between check and use) for reads and
  writes; listing a folder by name still reads its entries by path (an attacker needs write access to the repository).

`index.sqlite` is disposable and private (file 0600 in a 0700 directory, `secure_delete` on, symlinks refused). It
carries an SQLite `application_id` marker and a schema version in `PRAGMA user_version`. A damaged file or a different
schema version is deleted and recreated empty, but only if the file is clearly ours (zero-length or carrying the
marker); any other file at that path is never touched and start-up fails with a clear error. A recreate empties both
the search and the graph tables, so start-up must compare **both** `built_from()` values with `HEAD`. Recreation is
not coordinated with other processes using the file: rolling deploys with different schema versions are unsupported
(stop the old process first).

### Accepted risks (owner decision, 2026-11-25)

- **Token rows are not authenticated.** `state.sqlite` stores token rows (ceiling, scopes, revocation) in clear and
  unsigned; only the file mode (0600) and the directory (0700) protect them. Anyone who can write the file can raise
  a ceiling, add a scope (including `core`) or clear a revocation. The owner declined a per-row MAC and accepted this risk.
- **No rollback or replay protection for proposal rows.** The sealed payload binds the proposal id, but not its
  revision: restoring a whole older row (for example from a backup) passes every check.
- **Historical proposal payloads.** Backups and WAL snapshots from before Phase 4 may contain sealed submissions
  rejected after the initial write. Phase 4 scrubbed validation rejections at admission. Older backups can also contain proposals awaiting
  review when the backup was taken. The Phase 7 cutover preserves backups and leaves legacy rows inert.
- **A note raised to `restricted` leaves plaintext history.** The previous public or internal version stays in the
  content repository's git history and in backups; history is never rewritten. Agent writes and owner edits
  return a warning when this happens ([domain-model](domain-model.md)).
- The content repository's own `.git/config` is trusted (filter drivers, `core.sshCommand`, `receivepack`), and the
  writer lock only stops Cérebro's own writers: an outside edit between the clean-state check and the write is
  overwritten.

## 6. Frontend

- React + TypeScript + Vite. Data fetching through a small `api/` layer (the UI's port); components never call
  `fetch` directly. TanStack Query for caching.
- **Graph:** Sigma.js (WebGL) + graphology; ForceAtlas2 layout in a web worker. Node size by priority,
  color by area or type (toggle), filters by priority/status/area/sensitivity, local graph (N hops) on click.
- **Reader:** notes rendered as real HTML with `lang` set so browser translation works. Code blocks, inline
  code, paths, commands, note ids and links carry `translate="no"`.
- **Recent changes:** the private, restricted owner session sees who changed each note, when, and a bounded
  diff. History reads require visibility of the current note and both sides of an edit. Links in historical
  content receive the same visibility protection as links in the reader.
- **Editor (owner):** CodeMirror 6 with `[[link]]` autocomplete; saves go through `OwnerEditNote`.
- English only; no i18n library ([ADR-0009](adr/0009-english-only.md)).

## 7. Dependency policy and stack

Full policy in [ADR-0013](adr/0013-stack-and-dependency-policy.md). Summary:

| Area | Choice | Notes |
|---|---|---|
| Python | 3.14 (newest minor GA for ≥ 12 months at bootstrap) | `uv` for env + lockfile |
| Core | standard library only | `dataclasses`, `enum`, `re`, `typing` |
| Index | `sqlite3` (stdlib) with FTS5 | |
| Frontmatter | PyYAML (restricted `SafeLoader` / `SafeDumper` subclasses, never `yaml.load` with an unsafe loader) | adapter only (`cerebro.adapters.outbound.filesystem.yaml_io`) |
| Git | `git` CLI via `subprocess` | no git library |
| Crypto | `cryptography` (Fernet) | adapter only |
| Password hashing | `hashlib.scrypt` (stdlib) | single admin user |
| HTTP | Uvicorn and FastAPI | adapter and bootstrap only |
| MCP | official `mcp` Python SDK | adapter only |
| CLI | argparse (stdlib) | local owner commands, ADR-0017 |
| Quality | pytest, ruff, mypy (strict), import-linter | custom `stdlib_only` contract type in `backend/devtools/` |
| Web | Node 26.13.1 Active LTS, pnpm 12.22.0, React, TypeScript, Vite, TanStack Query, Sigma.js, graphology, CodeMirror 6 | |

All versions are pinned exactly in lockfiles. Exact versions are the newest stable releases published at least
7 days earlier ([ADR-0013](adr/0013-stack-and-dependency-policy.md)), and recorded here once the lockfile is
generated, so this table matches the lock.

### Pinned backend versions

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
| cryptography | 50.0.2 (runtime, `==` pinned; pulls cffi 2.1.1, pycparser 3.12) | Fernet / `MultiFernet` behind the `Cipher` port; adapter only (`cerebro.adapters.outbound.crypto`) |
| PyYAML | 6.0.3 (runtime, `==` pinned) | frontmatter and `schema/*.yaml` through a restricted `SafeLoader` (no anchors, aliases, tags, duplicate keys; everything but `null` stays text) and a quoting `SafeDumper`; adapter only (`cerebro.adapters.outbound.filesystem`) |
| types-PyYAML | 6.0.12.20261113 (dev, `==` pinned) | type stubs so `mypy --strict` can check the PyYAML adapter |
| mcp | 2.3.0 (released 2026-10-02) | official SDK for tool schemas, Streamable HTTP and dev stdio support |
| FastAPI | 0.165.0 (released 2026-11-18); annotated-doc 0.0.7 (released 2026-11-18) | HTTP API; annotated-doc is its only new transitive dependency |
| uvicorn | 0.54.0 (released 2026-09-25) | ASGI server for MCP listeners |

The Phase 3 dependency PR records the versions and upload dates of the full MCP and Uvicorn dependency trees.

### Pinned web versions

Node 26.13.1 and pnpm 12.22.0 build and test the static UI. React and React DOM 19.3.1,
TanStack React Query 5.108.0, Sigma 4.0.6, Graphology 0.26.0 and ForceAtlas2 0.10.1
provide the UI and graph. The editor uses CodeMirror state 6.7.6, view 6.43.15, commands 6.11.1,
lang-markdown 6.5.2 and autocomplete 6.20.4. Marked 18.2.1 and DOMPurify 3.4.18 render
and sanitize Markdown. TypeScript 7.0.2, Vite 8.3.8 and Playwright 1.65.1 compile, bundle and
test the frontend. The [Phase 5 inventory](plans/phase-5.md#4-dependency-proposal-and-verified-versions)
records the type packages, transitive distributions and UTC release dates for the committed
[web lockfile](../web/pnpm-lock.yaml).
