# Architecture

Cérebro follows a **hexagonal (ports and adapters)** architecture. The core (domain + application) knows nothing
about YAML, SQLite, git, HTTP, MCP or any LLM provider. See [ADR-0001](adr/0001-hexagonal-architecture.md).

Agents write directly through validated use cases ([ADR-0020](adr/0020-direct-agent-writes.md)). Known errors
in the operational parts of this document are listed in [Phase 6 of the roadmap](roadmap.md#-phase-6--deploy-hml-prod).

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
| `NoteRepository` | `get`, `list_refs` (body-free `NoteRef` snapshot), `list_notes` (strict: raises on any problem file), `scan()` (usable notes plus every problem file, each `stable` or not; see below), `head()` (the current content revision: the id the last `apply` returned, `None` when empty), and `apply(changes, message)`: write one or more notes and record **one** commit with its `Written-by` trailer, all-or-nothing; each `NoteChange` carries the `expected_hash` (`None` = must not exist) and a mismatch raises `ContentConflict` | filesystem; in-memory fake |
| `SearchIndex` | `upsert` / `remove` / `rebuild(notes, revision=)`, `built_from()` / `set_revision()` (the content revision the index reflects; `upsert`/`remove` never change it), `search(SearchQuery)` (ceiling mandatory; order: score, priority, id) and `find_similar` (duplicate detection); returns summaries only | SQLite FTS5 |
| `GraphIndex` | `rebuild(refs, revision=)`, `built_from()` / `set_revision()` (as `SearchIndex`), `graph`, `backlinks`, `neighbors(hops)`, `orphans`, `broken_links`; every query takes the ceiling, hidden notes are neither nodes nor broken links | SQLite tables |
| `VersionControl` | `push` to the backup remote. Commits are made by `NoteRepository.apply`, so a write and its commit cannot diverge | git CLI |
| `Cipher` | encrypt/decrypt restricted note payloads (used by the filesystem adapter only); `DecryptionError` on tampering | Fernet |
| `TokenStore` | `add`, `get`, `find_by_hash` (also returns revoked tokens), `save`, `all`; tokens are stored hashed | SQLite |
| `WriteAttemptStore` | Atomically reserve a rolling-hour slot for an authenticated token; a refusal leaves state unchanged | SQLite; in-memory fake |
| `ChangeHistory` | Return recent first-parent revisions, attribution and note versions for private owner history | Git/filesystem; in-memory fake |
| `Credentials`, `LoginAttempts`, `SessionStore` | Owner browser login: password verifier, shared attempt throttle, listener-bound sessions ([ADR-0019](adr/0019-owner-browser-api.md)) | crypto, SQLite, in-memory sessions; fakes |
| `Clock`, `IdGenerator` | `now()`; note ids, token ids and token secrets, for deterministic tests | system / fixed fakes |

Recent changes scans at most 32 first-parent revisions and shows up to 20 attributable note changes per page. The next cursor identifies the first unexamined revision. The application
checks current and historical visibility before returning the actor, timestamp, note and unified diff. Whole
documents are compared, and only the returned diff is bounded (16,384 characters), with clipping marked as
truncated; a clipped diff gives removed lines at most half of that bound. When the changed regions of both versions
are too large to align line by line (their line counts multiply to more than 4,000 x 4,000), the region is shown as
removed and then added. Git retains the complete history.

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
first) and apply `ctx.ceiling` (`include_history` only adds `superseded`/`archived`; `ReadNote` always serves them
by id, with their status, so history found in search or through a link can be read and links to it stay
complete). A hidden note, a missing note
and a hidden neighbors center all raise the same `NoteNotFound`; search results, hub member lists, neighbors and
the graph contain visible notes only and carry no totals, so nothing reveals a hidden note. `GetOverview` serves
current P0 notes in id order within the 800-word core budget (`core_truncated` when some do not fit) plus the
visible hubs and their areas. `ReadNote` accepts `id`, `id#Section` or `[[id#Section]]` (`SectionNotFound` for a
visible note without that heading, carrying
the headings of the redacted body: whole, distinct, at most 40 and 2048 bytes, which `cerebro_read` returns as
`headings` with `headings_truncated`) and, for a hub, returns the member list built at read time (ADR-0015). The
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
- `cerebro_write_guide` serves the versioned English writing instructions together with the allowed areas and
  types (`GetVocabulary`, `write` scope). See the
  [write decision](adr/0020-direct-agent-writes.md) and [domain contract](domain-model.md#7-agent-writes).

**Maintenance**
- `RebuildIndex` — re-read the content repo (`scan`), decrypt restricted notes in memory, rebuild the index (owner
  only; also the recovery path when an index update failed after a commit). Files with a stable problem are left
  out and the index is still recorded as built from `head()`; any problem that is not stable fails the rebuild and
  records nothing (see the filesystem notes below).
- `HealthReport` — orphans, broken links, hub chains, overdue `review_by`, budget violations, secrets in existing
  notes (never echoed), stale indexes (owner only, restricted ceiling). Problem note files are listed first, by
  path with a fixed reason (`note_file_excluded`, `note_file_blocking`); the report itself never fails because of
  a note file.
  An index is stale when its `built_from()` differs from the repository `head()` (`index_is_stale`; the
  `Container.start()` reindexes when it is stale, implemented in Phase 2 PR 8), or when its content
  differs note by note. `RebuildIndex` records `head()` (read before the notes); agent writes and
  `OwnerEditNote` upsert the committed note and only then advance the recorded revision of an index that was
  fresh before the commit (`index_commit`). A failed index update is not reported to the writer (the commit is
  durable); it leaves a revision behind `head()`. **Self-healing:** every use case that reads an index, and every
  writer once it holds the lock, first compares the revisions with `head()` (about 1 ms, one `git rev-parse`) and
  rebuilds under the writer lock when either is behind, which also picks up external commits without a restart.
  If that rebuild fails, the request fails: a stale index never serves a wider projection of a note that was
  raised in sensitivity or archived. No index state is evicted before a commit.
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
| Tokens and login and write limits | state dir, `state.sqlite` | no | yes (file backup) |
| Fernet key, admin password hash | secrets dir | no | owner keeps an offline copy of the key |

Filesystem repository, operating notes (Phase 2):

- Only committed note content is served. A note file under `core/`, `hubs/` or `notes/` that differs from `HEAD`
  (staged, edited, deleted, untracked or ignored: an abandoned hand edit, an orphan left by an interrupted create, a
  note left staged by a crash) is never read into an index: `get` raises for it, and `scan` reports it as a problem
  that is **not stable**. At startup and before writes or index rebuilds, the service serializes recovery with
  writers and backs up working files, staged changes and deletions (including untracked and ignored files) in a
  private timestamped `quarantine-*` directory under state. It then restores the affected paths to HEAD. Each
  directory contains `worktree.tar`, `staged.patch` and the base commit followed by NUL-separated Git status
  entries in `status`. The original inodes remain under `originals/` in that directory (or under the same
  `quarantine-*` name inside the content repository's `.git/` on separate volumes). This preserves writes through
  editor descriptors that were open before recovery. Archives and originals contain the owner's exact bytes,
  including any plaintext. Keep these directories local like the derived index: exclude `quarantine-*` and
  `cerebro-quarantine-in-progress` from external state and filesystem backups. Git pushes exclude them.
  Recovery installs HEAD without replacing a later editor save. After an interruption inspect the state directory
  and `.git/cerebro-quarantine-in-progress`. Health lists completed and incomplete (`.partial`) snapshots.
  If another save prevents restoration, `unrestored-paths` lists the affected names separated by NUL bytes.
  Independent paths are restored before the error is reported.
  The service never prunes them.
  Listings check before and after reading; `get` checks after reading and, when the file looks uncommitted, waits for
  any running `apply` and checks again. Residual risk: index flags set by hand (`assume-unchanged`, `skip-worktree`)
  hide a file's edits from git, so never set them on note files.
- **One invalid committed file does not take the others down.** A file that equals `HEAD` but is not a valid note
  (malformed frontmatter, wrong folder or file name, a plaintext file declaring `restricted`, an oversized file) is a
  **stable** problem: the same revision always gives the same result, so it is left out of `scan` and the index is
  recorded as built from `head()` without it (no rebuild per request). Every copy of an id stored in several folders
  is left out, so neither copy can stand for the id. `get` returns `None` for such an id: it reads as missing
  everywhere (search, graph, reads, link targets are redacted), so exclusion only ever narrows what is served. Not
  stable, hence fail-closed: a restricted note that cannot be decrypted (the result depends on the configured key),
  a symlinked or unreadable file, a folder that is not a real directory.
- Writes validate the candidate, cross-note links and P0 budget against the usable committed notes. A stable
  problem blocks editing that id or linking to it, including links in code, titles and summaries. Other notes
  remain writable. Unstable scans still prevent writes. The owner sees excluded files in Health and can repair
  them in the content repository. Server failures release the agent's quota reservation. Broken references
  consume a slot if any failed id is missing or hidden. A request blocked solely by an excluded id releases its slot.
  The response uses the same unavailable-target or link finding in all three cases. Quota exhaustion can therefore
  distinguish an excluded file from a missing or hidden id. Repaired notes enter the P0 budget on the next scan.
- Below the `restricted` ceiling, a note file that cannot be served now (`NoteReadError` on `get`) reads exactly like
  a missing note, so an agent cannot learn that a hidden note exists; at `restricted` the error propagates.
- `apply` refuses when a `.git/index.lock` exists (the error tells the owner to remove a stale one), when anything is
  staged (the paths are named), or when a planned note is untracked, has uncommitted edits, or a new note's path is
  already known to git, and commits only the planned paths.
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

`state.sqlite` is not disposable and has a single schema version. A new empty file is initialised; a file with any
other version, including those of releases before [ADR-0023](adr/0023-no-legacy-compatibility.md), is refused with
an error naming its path and version, and is never deleted, upgraded or repaired. Move it aside and issue new tokens.

### Accepted risks (owner decision, 2026-11-25)

- **Token rows are not authenticated.** `state.sqlite` stores token rows (ceiling, scopes, revocation) in clear and
  unsigned; only the file mode (0600) and the directory (0700) protect them. Anyone who can write the file can raise
  a ceiling, add a scope (including `core`) or clear a revocation. The owner declined a per-row MAC and accepted this risk.
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
test the frontend. The [Phase 5 inventory](plans/archive/phase-5.md#4-dependency-proposal-and-verified-versions)
records the type packages, transitive distributions and UTC release dates for the committed
[web lockfile](../web/pnpm-lock.yaml).
