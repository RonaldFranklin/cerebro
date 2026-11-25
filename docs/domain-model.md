# Domain model

This document defines the knowledge model. It is the specification for `cerebro.domain`.

## 1. Content repository layout

The content repository is separate and private ([ADR-0003](adr/0003-two-repositories.md)).
Folders are shallow on purpose: structure lives in metadata and hubs, not in the directory tree.

```
<content-repo>/
├── core/        # P0 notes — always loaded by agents, strict word budget
├── hubs/        # P1 hub notes: introduction only, one per area or sub-area (members computed, ADR-0015)
├── notes/       # all other notes (P2/P3), flat, file name = note id
└── schema/
    ├── areas.yaml   # controlled list of areas (only the owner adds areas)
    └── types.yaml   # controlled list of user note types (core and hub are built in)
```

`AGENTS.md`-style entry text is **not** stored here; agents get it from `GetOverview`.

## 2. Note

A note is a Markdown file with YAML frontmatter. File name is `<id>.md`.

```yaml
---
id: server-ssh-access            # kebab-case, unique, immutable, equals file name
title: SSH access to the home server
type: runbook                    # from schema/types.yaml
area: infrastructure             # from schema/areas.yaml
priority: P2                     # P0 | P1 | P2 | P3
sensitivity: restricted          # public | internal | restricted
status: verified                 # see section 5
summary: >-                      # ≤ 300 characters, says what the note answers
  How to reach the home server over SSH from the workstation, including
  the key in use and how to verify the host before making changes.
hub: infrastructure              # id of the hub this note belongs to
related: [server-docker-conventions]
superseded_by: null              # required when status = superseded
lang: en
created: 2026-11-24
updated: 2026-11-24
review_by: 2027-05-24            # optional; health report flags overdue notes
---

# SSH access to the home server
...
```

### Field rules

| Field | Required | Rule |
|---|---|---|
| `id` | yes | `^[a-z0-9]+(-[a-z0-9]+)*$`, ≤ 80 chars, equals file name, immutable |
| `title` | yes | ≤ 120 chars |
| `type` | yes | built-in (`core`, `hub`) or in `schema/types.yaml` |
| `area` | yes | in `schema/areas.yaml` |
| `priority` | yes | P0–P3 |
| `sensitivity` | yes | public / internal / restricted |
| `status` | yes | see section 5 |
| `summary` | yes | 40–300 chars; must describe what the note answers |
| `hub` | yes except for P0 and hubs (optional for a hub: its parent hub) | must reference an existing hub |
| `related` | no | existing ids |
| `superseded_by` | iff `status: superseded` | existing id, not itself, no cycles |
| `lang` | no | default `en` |
| `created`, `updated` | yes | ISO dates, `updated ≥ created` |
| `review_by` | no | ISO date |

Structure and rule details enforced by the domain:

- **Strict frontmatter.** Unknown keys are errors. A key whose value is `null` counts as missing (default for
  optional fields, error for required ones). Dates are `YYYY-MM-DD`; timestamps are rejected. Frontmatter that
  is not a mapping (empty, list, scalar) is reported, not a crash. Values YAML 1.1 turns into booleans or
  numbers (`no`, `on`, `2026`) are rejected with a hint to quote them.
- **Type decides priority:** `core` ⇒ `P0`, `hub` ⇒ `P1`, every other type ⇒ `P2` or `P3`. Rules that depend
  on the kind of note (hub requirement, body budget) key on the type. P0/core notes cannot be `restricted`.
- **`hub` field:** required for every type except `core` and `hub`; optional for a hub (its parent hub);
  **not allowed** for `core` notes (they are global, not part of an area).
- `title` is not blank, a single line, and ≤ 120 characters after trimming. `summary` length is also measured
  after trimming. `lang` is not blank.
- `hub`, `related` and `superseded_by` cannot reference the note itself. Duplicate ids in `related` are
  flagged.
- **Severity depends on the write path.** Rules report findings with a stable code and never stop at the first
  one. A severity policy per write path decides which findings are `error` (block the change) and which are
  `warning` (reported, e.g. by the health report, but not blocking). Proposals and owner edits share the same
  rules; today both treat only the soft word limit and duplicate `related` ids as warnings; **broken links are
  additionally a warning for owner edits** and an error for proposals. Structural frontmatter problems are
  always errors.
- Enforced outside the domain: `id` equals the file name (filesystem repository adapter, Phase 2); `id` is
  immutable (`OwnerEditNote` / `ApplyProposal`).
- **Cross-note rules** are pure functions over a snapshot `Mapping[NoteId, NoteRef]` (see Links); the proposed
  note's `NoteRef` replaces the stored one when validating a change. They report only problems that involve the
  validated note; a health report runs them for every note.

### Links

- Body links use wikilinks: `[[note-id]]` or `[[note-id#Section heading]]`.
- The domain extracts links from the body plus `hub`, `related` and `superseded_by` to build the graph.
- Links inside fenced code blocks and inline code are examples and are ignored; so are targets that are not
  valid note ids. The `[[id|alias]]` form is not supported. Duplicate links are collapsed.
- A link (body, `related`, `superseded_by`) to a missing id is a **broken link** (`broken_link`: warning for
  owner edits, error for proposals). Section anchors are not checked against the target (that needs its body).
- A `hub` that does not exist (`hub_not_found`) or whose type is not `hub` (`hub_not_a_hub`) is always an error.
- `superseded_by` chains that lead back to the note are a `superseded_by_cycle` error.
- `NoteRef` is the body-free view used by these rules: id, title, type, priority, sensitivity, status, `hub`,
  `related`, `superseded_by` and the wikilinks already extracted from the body.
- **Sections.** `ReadNote` reads one section by heading: ATX headings (`#` to `######`), matched ignoring case
  and extra whitespace, first match wins; the section includes its sub-sections and ends at the next heading of
  the same or a higher level. Headings in fenced code blocks do not count.

### Hubs

See [ADR-0015](adr/0015-hub-membership-computed-at-read-time.md).

- A hub note (`type: hub`, `priority: P1`, in `hubs/`) stores only its **introduction**; the 800-word budget
  applies to it.
- Members are the notes whose `hub` field points to the hub. The list is never stored in the file: it is built
  at read time from the index, filtered by the effective ceiling, without `superseded`/`archived` notes unless
  `include_history` is set, and ordered by priority, then title.
- Member entries carry `id`, `title`, `type`, `priority`, `status`, `summary` — never the body.
- A hub may set `hub` to a parent hub (sub-area). Hub chains must be acyclic and at most **3 hubs** deep
  (area hub + two sub-area levels); violations (`hub_chain_cycle`, `hub_chain_too_deep`) are validation errors
  on every write path. A hub counts itself: a hub three levels under an area hub is the 4th and is rejected.

### Note types

- **Built-in:** `core` (P0 notes) and `hub`. Domain rules depend on them, so they are always allowed and are not
  listed in `schema/types.yaml`. Declaring them there anyway is accepted and does not duplicate them.
- **User types** live in `schema/types.yaml`. The initial list written by `init-content` is `concept`, `guide`,
  `runbook`, `reference`, `config`, `decision`, `project`.

## 3. Priority (context budget)

See [ADR-0004](adr/0004-priority-levels-and-context-budget.md).

| Level | Meaning | Served | Budget |
|---|---|---|---|
| P0 | Core: global rules and facts every agent must know | always, by `GetOverview` | **all P0 together ≤ 1,500 words** |
| P1 | Hubs: area introduction; member list computed at read time | when an area is relevant | introduction ≤ 800 words each |
| P2 | Regular notes | on search / link | soft 1,500 words, hard 3,000 |
| P3 | Reference, archive, raw material | only on explicit search | same as P2 |

Additional rules:
- P0 notes cannot be `restricted`.
- Search results contain only `id`, `title`, `type`, `area`, `priority`, `status`, `summary`.
- `ReadNote` supports reading a single section by heading.
- Notes above the soft limit are flagged by the health report; above the hard limit they are rejected.
- **Word count** = number of whitespace-separated tokens in the body (frontmatter excluded; Markdown syntax and
  code blocks count like any other text). P0 notes have no individual limit, only the combined one, which
  counts only **current** P0 notes (the ones `GetOverview` serves), with a proposed version replacing the
  stored one when a change is validated.

## 4. Sensitivity and access ceiling

See [ADR-0005](adr/0005-sensitivity-and-access-ceiling.md).

Order: `public < internal < restricted`.

```
effective_ceiling = min(listener_ceiling, token_ceiling)
```

- **Listener ceiling:** private listener (Tailscale) = `restricted`; public listener (Cloudflare) = `internal`.
- **Token ceiling:** set when the owner issues the agent token.
- Notes above the effective ceiling are **invisible**: not in search, not in hubs, not in neighbors, not in
  the graph. Reading one by id returns "not found" (no existence leak).
- `restricted` notes are encrypted at rest in the content repository ([ADR-0008](adr/0008-encryption-of-restricted-notes.md)).

## 5. Status

| Status | Meaning | Served as current? |
|---|---|---|
| `current` | Valid knowledge (non-operational) | yes |
| `proposed` | Planned change/config, not done yet | yes, clearly labeled |
| `applied` | Done, not verified | yes, labeled |
| `verified` | Done and tested | yes |
| `superseded` | Replaced; `superseded_by` required | **no** — only with `include_history` |
| `archived` | Kept for history only | **no** — only with `include_history` |

This encodes two rules: *prefer the current state over history*, and *distinguish planned, applied and
tested*.

## 6. Secrets

Notes must never contain secrets. They may say **where** a secret lives (e.g. "password manager, item X"),
never its value. The domain has a secret scanner (regex patterns for private keys, common token formats,
`password=`-style assignments, high-entropy strings) applied to every proposal and owner edit. A hit blocks the
change.

## 7. Proposals

See [ADR-0006](adr/0006-write-path-inbox-curator.md).

```yaml
kind: create | update | link | deprecate
target: <note id>              # required for update, link, deprecate
title: ...
body: ...                      # full body for create; full new body or patch for update
suggested:
  area: ...
  type: ...
  priority: ...
  sensitivity: ...
rationale: why this belongs in Cérebro
source:
  agent: codex@workstation      # derived from the token, not trusted from payload
  session: <free text>
  context: <free text>
```

### Lifecycle

```
received → validated ─┬─▶ curated ─┬─▶ approved ─▶ applied
                      │            ├─▶ rejected
                      │            └─▶ needs_owner ─▶ approved | rejected
                      └─▶ rejected (validation failed)
```

- **Validation** (deterministic): schema, budgets, secret scan, duplicate detection (FTS similarity against
  existing titles/summaries), target existence.
- **Curation** (`Curator` port): decides `reject`, `merge_into(<id>)`, `create`, or `needs_owner`, and
  produces the final note(s): normalized to English, frontmatter filled, links added. The curator never
  writes; it returns a `Decision` with a diff.
- **Approval:** initially the owner approves every decision. An autonomy policy can later auto-approve
  low-risk decisions (e.g. P3, public, link-only).
- **Application:** `ApplyProposal` writes files and commits with trailers:

```
Add note: server-ssh-access

Proposed-by: codex@workstation
Curated-by: curator/<model>
Approved-by: owner
Proposal-Id: <id>
```

## 8. Agent tokens

- One token per agent (e.g. `claude-code@workstation`, `codex@workstation`).
- Attributes: name, scopes (`read`, `propose`), ceiling, created, last_used, revoked.
- Stored hashed (SHA-256; tokens are high-entropy random). Shown once at creation.
- The token name becomes `source.agent` on proposals.
