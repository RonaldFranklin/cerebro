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
  is not a mapping (empty, list, scalar) is reported, not a crash. The filesystem adapter reads every unquoted
  scalar as literal text (no YAML 1.1 coercion: `no`, `on`, `2026` stay text) and always writes such strings
  quoted, so files round-trip exactly. `parse_note` still rejects non-text values (booleans, numbers) that reach
  it from other sources, with a hint to quote them.
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
  rules; today both treat only the soft word limit, duplicate `related` ids and `possible_duplicate` as warnings; **broken links are
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
- **No link to a higher sensitivity.** A note may not link to a note of **higher** sensitivity than its own, through
  body wikilinks, `hub`, `related` or `superseded_by` (`link_to_higher_sensitivity`). Bodies are served as written,
  so such a link would hand the id of a restricted note to every reader whose ceiling covers the source but not the
  target. It is an **error on every write path**: unlike `broken_link` it is never a warning, not even for owner
  edits (it is in no severity policy's warning set). The body check covers every `[[id]]` that read-time redaction
  treats as a link, including inside code spans and blocks and the `[[id|alias]]` form. The rule also runs
  **inbound**: when a note is created or its sensitivity rises (proposals and owner edits alike), stored notes of lower sensitivity that link to it
  (body, `hub`, `related`, `superseded_by`) are errors, reported as "<linking-id> links to this note". Equal or lower sensitivity is fine. A missing target is a
  `broken_link`, not this. The message names only the target id as the author wrote it (never its title or
  sensitivity); the report goes to the owner and the curator, never to the submitting agent. The health report runs
  the rule too, so legacy leaks are listed as errors.
- `title` and `summary` may not contain `[[` (`wikilink_in_metadata`, error on every write path): search results
  and the overview serve them without link redaction.
- A `hub` that does not exist (`hub_not_found`) or whose type is not `hub` (`hub_not_a_hub`) is always an error.
- `superseded_by` chains that lead back to the note are a `superseded_by_cycle` error.
- **Redaction on read (defense in depth).** `ReadNote` replaces every `[[...]]` in the served body (whole note or one
  section) whose target is a note id the reader cannot see (above the effective ceiling, history without
  `include_history`, or missing) with the constant `[[redacted link]]` (`cerebro.domain.links.REDACTED_LINK`): the whole
  link goes, anchor and alias included, and code blocks are covered too. Brackets that are not note ids are left
  alone. `hub`, `related` and `superseded_by` of the `NoteView` and hub `members` were already filtered to visible
  targets. `GetOverview` redacts the P0 bodies it serves in the same way. This protects against data that predates
  the rule or was edited outside Cérebro. The marker's text is not a valid note id, so it is never a link.
- Escaped or entity forms (`\[\[x]]`, `&#91;&#91;x]]`) are plain text, not links: neither the rules nor redaction treat
  them as such, and the UI must not render them as links.
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
- **Visibility rule** (`cerebro.domain.access.is_visible`): a note is visible when its sensitivity is `<=` the
  effective ceiling and its status is current, or `include_history` is set (`superseded` / `archived`).
  `include_history` never raises the ceiling. Callers treat "not visible" exactly like "does not exist".
- **Fail closed:** `effective_ceiling(listener, token)` has no defaults; wrongly typed ceilings raise, and
  `is_visible` / `AccessCeiling.allows` / `AccessContext.can` deny any value of an unexpected type.
- **Principals and scopes:** `AccessContext(principal, ceiling, scopes)` is built once per request, with
  `ceiling` already the effective ceiling. `principal` is `Agent(name)` or `Owner`. Scopes are `read` and
  `propose`. The **owner has every scope** but is still limited by the **listener** ceiling (there is no token);
  owner-only actions (owner edit, review, maintenance) require the `Owner` principal, not a scope. An agent only
  has the scopes of its token. Agent names are lowercase (`claude-code@workstation`, at most 64 characters);
  `owner` (with or without a host part) is reserved so an agent can never appear as the owner in trailers.
- `restricted` notes are encrypted at rest in the content repository ([ADR-0008](adr/0008-encryption-of-restricted-notes.md)).

### Raising a note to `restricted`: previous versions stay in history

Every applied change is a git commit and history is never rewritten. When an **existing** note's sensitivity
rises to `restricted` from `public` or `internal` (through an `update` proposal in `ApplyProposal`, or through
`OwnerEditNote`), the result carries a warning `ApplyWarning(code=PREVIOUS_VERSION_IN_HISTORY, message=...)`
(`ApplyProposalResult.warnings`, `OwnerEditNoteResult.warnings`): the earlier, less sensitive plaintext version
remains in the content repository's git history and in any backups, so the new level protects only the current
version. The message is a fixed text with no note content. No warning is raised for a create, for an unchanged or
lowered sensitivity, for a note that was already `restricted`, for an `already_applied` retry, or when nothing was
written (validation errors, conflicts). Scrubbing history (and backups) is a manual owner operation, out of scope
for Cérebro.

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
never its value. The domain has a secret scanner (`cerebro.domain.secrets`, pure standard library) that
`validate_note` runs on every proposal and every owner edit. A hit is a `secret_detected` violation, an
**error under both severity policies**.

**What is scanned.** Every text field of the note: `title`, `summary`, `id`, `type`, `area`, `lang`, `hub`,
`related`, `superseded_by` and the whole `body`, including fenced code blocks (no "example" exemption). Frontmatter
keys outside the schema are rejected before a note exists, so their values never reach the scanner or a note.

Each field is normalized before scanning (NFKC, so full-width look-alikes become ASCII; invisible format
characters such as zero-width spaces and soft hyphens are removed). At most 20 findings are listed per field, plus
one summary finding; every pattern runs in linear time on long lines.

**Detectors** (line by line, first detector wins on overlapping text):

- PEM / OpenSSH / PGP private key headers (`-----BEGIN ... PRIVATE KEY-----`);
- token formats: AWS access key ids, GitHub (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_`), GitLab
  (`glpat-`), Slack (`xox[abprs]-`), Stripe live keys (`sk_live_`, `rk_live_`), Google API keys (`AIza...`),
  Anthropic / OpenAI keys, npm, SendGrid, JSON Web Tokens, `Bearer <token>` (must contain a digit);
- passwords inside URLs (`scheme://user:secret@host`);
- `password=` / `secret:` / `api_key=` / `token=` style assignments (also `DB_PASSWORD=`, `dbpassword=`, `senha:`,
  JSON `"password": "..."`)
  with a non-trivial value: at least 8 characters, not a placeholder (`<...>`, `${...}`, `xxx`, `changeme`,
  `example`, references to a password manager or environment, ...). Unquoted values must also look like a credential
  (digit, mixed case or symbol), so `secret: vaultwarden-item-name` is not flagged; quoted values are flagged
  regardless;
- high-entropy strings: a token of at least 32 characters from `[A-Za-z0-9+/_=-]` with letters and digits and
  Shannon entropy of at least 4.3 bits per character. Hex-only strings (commit hashes, digests), UUIDs and
  wikilinks are never flagged.

**Reports never contain the secret.** A finding carries the detector kind, the field, the line number and, for
secrets of 16 characters or more, the first two characters. Thresholds are constants in the module. `parse_note`
reports a rejected frontmatter value (or unknown key) that the scanner flags as `secret_detected` instead of
quoting it.

**Errors and report messages never contain a received value.** Exceptions from constructors and parsers
(`NoteId("...")`, `Priority.parse(...)`), application errors (`NoteNotFound`, `ProposalNotFound`, ...) and
`Violation`/`Finding` messages name only the field and, where useful, the type or length of what was received.
Allowed-value lists are fine. Exactly what is and is not named:

- Never named: received values, unknown frontmatter keys (reported as `unknown field #N`, with length, N being
  the 1-based order of appearance), link, `related`, `hub` and `superseded_by` targets (reported by position:
  `link #N of body`, `related entry #N`), and any id on a `superseded_by` or hub chain (reported by length:
  `4 hubs deep (maximum 3)`), because chains walk the unfiltered snapshot. Duplicate findings say
  `similar to an existing note (NN%)`, and orphan-proposal findings say `targets a note that does not exist`.
- Named, by exception: the id of a *stored* note that links to the one being checked, in the inbound-link
  sensitivity finding (`<id> links to this note`). That note is by definition below the checked note's
  sensitivity, so naming it reveals nothing the reader could not see; and health-issue subjects (stored note or
  proposal ids in the owner-only health report).
- Exception chains carry no value either: handlers that catch a stdlib error raise the replacement outside the
  `except` block, so `__context__` does not keep the received value (the sweep test checks `__cause__`,
  `__context__` and `__notes__`).
A sweep test feeds a secret-looking marker through the constructors and asserts it appears nowhere in
`str`, `repr`, `args` or report messages. Typed attributes such as `NoteNotFound.note_id` stay for adapters, but
are not in the message.

**Known limits.** The scanner is best-effort, not a guarantee. It misses secrets split across lines (including
YAML block values such as `password: |`) or encoded (base64, hex, reversed), short or purely lowercase unquoted
passwords, low-entropy secrets, secrets in prose (`the password is hunter2`), test-mode keys (`sk_test_`), unknown
token formats shorter than 32 characters, **hex-only keys** (many API keys are 32 or 40 hex characters; they are
caught only inside a `key=`-style assignment) and a large share of random tokens drawn only from lowercase letters
and digits (about half of such 32-character tokens, about one in seven at 40 characters, fall below the entropy
threshold). It flags long random-looking identifiers that are not secrets, such as base64 digests (`sha256-...`
integrity hashes) and SSH public keys; the owner then rewords the note. The owner
review step remains the last line of defence.

## 7. Proposals

See [ADR-0006](adr/0006-write-path-inbox-curator.md).

```yaml
kind: create | update | link | deprecate
target: <note id>              # required for update, link, deprecate; absent for create
base_hash: <sha256 hex>        # required for update, link, deprecate (see "Content hash")
title: ...
body: ...                      # full body for create; full new body or patch for update
suggested:
  area: ...
  type: ...
  priority: ...
  sensitivity: ...
  summary: ...                  # 40-300 characters
  hub: <hub id>
rationale: why this belongs in Cérebro
source:
  agent: codex@workstation      # derived from the token, not trusted from payload
  session: <free text>
  context: <free text>
```

### Lifecycle

```
received ─┬─▶ validated ─▶ curated ─┬─▶ approved ─▶ applied
          │                         ├─▶ rejected
          │                         └─▶ needs_owner ─▶ approved | rejected
          └─▶ rejected (validation found errors)

approved ─▶ needs_owner   (conflict found when applying: flagged)
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

### Write use cases (application layer)

Authorization lives in the use cases, never in the adapters:

| Use case | Who | Notes |
|---|---|---|
| `SubmitProposal` | an `Agent` with scope `propose` | The owner may **not** submit (no impersonation). `source_agent` is the context's agent name; the request has no such field. |
| `ValidateProposal`, `CurateProposal`, `ReviewProposal`, `ApplyProposal`, `OwnerEditNote` | `Owner` only | Validate and curate are steps of the owner-controlled pipeline: the composition root runs them with an owner context, a token can never trigger them. |

- **Ceilings on the write path.** An agent cannot probe or change notes above its ceiling: the target of
  `update` / `link` / `deprecate` must be visible to it (history included), otherwise `NoteNotFound`, identical to
  a missing note. A suggested `sensitivity` above the agent's ceiling is `PermissionDenied`. A proposal cannot
  **lower** the sensitivity of an existing note (a validation error), so an agent cannot declassify what it
  can read. The owner cannot write above the ceiling of the listener it uses (`PermissionDenied`), and sees
  hidden targets as `NoteNotFound` too.
- **Secrets never reach the store.** `SubmitProposal` scans every free text of the payload (title, body, summary,
  rationale, session, context). On a hit it stores a **scrubbed**, already `rejected` proposal (placeholders
  instead of text) and returns the redacted report. `ValidateProposal` scans again, and **every** proposal it
  rejects is stored scrubbed (not only those holding a secret): only the kind, target, base hash, source agent,
  timestamps and the structured findings (codes, fields, detectors, line numbers) are kept; every free text and
  every suggested field (title, body, summary, rationale, area, type, priority, sensitivity, hub, superseded_by,
  session, context) is replaced by a placeholder or dropped (a `link` keeps `related`, which the domain requires).
  Known property: `SubmitProposal` stores the raw payload **before** validation, sealed at rest in `state.sqlite`;
  a validation rejection then overwrites it. The store checkpoints the WAL after every write, but sealed (never
  plaintext) residue may remain in the WAL or in backups until a checkpoint completes. Reports and results never quote proposed content.
- **State at rest.** `state.sqlite` seals the whole proposal (content, target, report, decision) with the
  `Cipher`, even for `public` proposals, binding the proposal id into the sealed payload; only id, kind, status,
  revision, source agent, timestamps and the curator/approver names are in clear, for queries and the
  compare-and-swap. Tokens are stored by SHA-256 hash only. There is no rollback or replay protection for
  proposal rows (each row is authentic on its own, so restoring the whole file or one row from an older backup
  passes), and token rows are plain, unauthenticated columns protected only by the `0600` file mode.
- **From proposal to note.** The note a proposal would write is built deterministically (`build_candidate`), the
  same way for validation and for apply. `create` needs `title`, `body`, `type`, `area`, `priority`,
  `sensitivity` and `summary` from the agent (`hub` where the type requires one; missing ones are
  `missing_field` errors); the note starts `current`, English, dated today, and its id is the curator's /
  owner's `create` target (validation uses an id derived from the title). `update` replaces the body and any
  metadata given; `link` and the `related` of an update only **add** ids; `deprecate` makes the note
  `superseded` (with `superseded_by`) or `archived`. `superseded_by` is only valid for `deprecate`.
- **Validation.** Per-note rules and the secret scan over the resulting note, cross-note rules over the snapshot
  with the proposed note substituted, the combined P0 budget, and, for `create`, duplicate detection through
  `SearchIndex.find_similar` (`possible_duplicate`, **a warning**: similarity at least 0.5, reported to the owner and
  curator only).
- **Curation.** `NullCurator` (default) answers `needs_owner`, suggesting the target (or an id derived from the
  title for a `create`); a `needs_owner` decision moves the proposal to `needs_owner`. A curator decision that does
  not fit the proposal (another target, wrong shape for the kind) is replaced by `needs_owner`.
  The curator never receives restricted notes ([ADR-0016](adr/0016-curator-never-sees-restricted.md)): its context
  ceiling is `min(request ceiling, internal)`, and a proposal that creates, changes or links a restricted note (own
  sensitivity, target, `hub`, `related`, `superseded_by` or body wikilinks) skips it: the decision is `needs_owner`
  with a generic reason and `Curated-by: curator/policy`.
- **Review.** The owner approves (optionally `override`ing the decision, e.g. to name the id of a note to create) or
  rejects (reason required). Approval is refused when the decision could not be applied.
- **Apply.** `decision.target` must equal `proposal.target` for `update` / `link` / `deprecate`; a `create` needs a
  `create` (or owner-approved `needs_owner`) decision naming the id. Then, before writing anything: `base_hash`
  against the stored note's content hash (a `create` target must not exist), then validation again with the proposal
  policy. A mismatch or an error sends the proposal back to `needs_owner` with `conflict` set (`conflict` /
  `invalid` outcomes) and **writes nothing**. Then one commit, `applied`, and the derived indexes are updated.
  **Races and retries:** the proposal carries an integer `revision` (0 on creation; domain transitions never touch
  it). `ProposalStore.save(proposal, expected_revision=n)` is a compare-and-swap: it stores the proposal with
  revision `n + 1` only if the stored revision is `n`, otherwise raises `ProposalConflict`. Every use case that
  saves passes the revision of the copy it loaded and does not swallow the conflict, except in `ApplyProposal` when
  the stored proposal is already `applied`. Of two racing reviews, validations or curations exactly one wins; the
  other gets `ProposalConflict` and must reload. `ApplyProposal`
  commits the note first (guarded by the content-hash compare-and-swap) and saves the proposal second, so the one
  non-atomic window is a lost proposal save after a successful commit. Two racing applies commit at most once; the
  loser finds the note changed, recognizes that writing the proposal would change nothing but the `updated` date,
  and if the winner already saved `applied` it simply reports `already_applied`. If the proposal was changed by
  something else in that window, `ProposalConflict` is raised after the commit; a crash there has the same effect.
  In both cases the proposal is not `applied` while the note is committed; retrying `apply` is safe (if the proposal
  is still `approved` it ends in `already_applied` without a second commit), otherwise the owner resolves it.
  Resolving a conflict is currently done by rejecting and re-proposing; a `rebase` use case comes later (the domain method exists).
- **Owner edits.** `OwnerEditNote` takes the full new note and the hash the owner loaded (`None` to create);
  `ContentConflict` if the note changed, `id` immutable, `created` kept, `updated` today. It uses
  `OWNER_EDIT_POLICY` (broken links warn) and the same secret scan; errors are returned and nothing is written. The
  commit carries `Proposed-by: owner`, `Curated-by: owner`, `Approved-by: owner` and `Proposal-Id: owner-edit-<id>`
  (`owner` is only accepted as `Proposed-by` when the owner is also curator and approver).
- **Names in the audit trail cannot be impersonated.** Agent names never contain `/` and `owner` is reserved, so
  an agent cannot appear as `owner` or as a curator. Every `Curated-by` value starts with the mandatory prefix
  `curator/` (e.g. `curator/null`, `curator/anthropic-triage`); the only exception is `owner` in an owner edit.
- **Approver and curator shape.** `Approved-by` is always `owner` for now (deliberate, until an autonomy policy
  with its own ADR defines other approvers); `Curated-by` must match `curator/[a-z0-9][a-z0-9._-]*`.
- **Token clock skew.** A token whose `created_at` is more than 5 minutes ahead of the verifier's clock
  (`CLOCK_SKEW_TOLERANCE`) is not valid and fails authentication like an unknown token.
- **Secret findings are structured.** The scanner's `Finding` carries `detector` and `line` (kept on the
  violation); there is no preview of the value at all. Consumers never parse the human `message`: the health
  report describes secrets from `detector` and `line` only.
- The `Vocabulary` (`schema/*.yaml`) is passed to the use cases that validate, loaded once by the composition root;
  it is a value, not a port.

### Rules in the domain

- `Proposal` is immutable; each transition (`mark_validated`, `reject_invalid`, `mark_curated`, `mark_needs_owner`,
  `approve`, `reject`, `rebase`, `override_decision`, `mark_applied`) returns a new instance, or raises `InvalidTransition`
  (a `DomainError` in `domain.errors`; this is the "typed `InvalidTransition`" the use cases let propagate).
  `applied` and `rejected` are terminal. `received → rejected` is added to the diagram: validation errors reject
  directly (`mark_validated` requires a report without errors).
- Required fields per kind: `create` has title and full body, no `target`, no `base_hash`; `update` has `target`,
  `base_hash` and the **full** new body (a patch format needs a future ADR); `link` has `target`, `base_hash` and
  non-empty `related`; `deprecate` has `target` and `base_hash`. Metadata fields (area, type, priority,
  sensitivity) are suggestions.
- `Decision` variants: `reject` (reason required), `merge_into` (target required), `create` (target = id of the
  note to create, required), `needs_owner` (reason required, suggested target optional). A `curated` proposal can
  only be approved when its decision is `merge_into` or `create`; the owner can approve any `needs_owner` one that names a note id to commit. An agent can never curate or
  approve its own proposal, an `approved`/`applied` proposal always carries a decision, curator and approver, and
  sending an approved proposal back to the owner (conflict) voids the approval.
- **Content hash.** `content_hash(note)` = SHA-256 hex of the UTF-8 JSON `{"frontmatter": note_to_raw(note),
  "body": body}` (`ensure_ascii=False`, compact separators, canonical key order, ISO dates, empty optionals
  omitted, body byte for byte, lone surrogates encoded as-is). Optimistic concurrency: `ensure_unchanged(expected_hash, current_note)` raises
  `ContentConflict`. `ApplyProposal` on conflict calls `mark_needs_owner(conflict=True)`; the owner then
  `rebase`s (new `base_hash`) before approving again. `OwnerEditNote` uses the same check.
- **Commit trailers** are validated, never rewritten: the subject is built from the note id (never from titles or
  free text); `Proposed-by` must be a valid agent name (so `owner` can never be forged there); `Curated-by` and
  `Approved-by` accept only letters, digits and `. _ / @ + -` (max 100); no newline or control character can
  reach a trailer. `Proposal-Id` is the proposal id.

## 8. Agent tokens

- One token per agent (e.g. `claude-code@workstation`, `codex@workstation`).
- Attributes: id, agent name, scopes (`read`, `propose`, at least one), ceiling, created, optional expiry,
  optional revocation time, token hash. (`last_used` is operational data kept by the token store, not part of the
  domain entity.)
- A token is valid when it is not revoked and `now < expires_at`; `now` is always passed explicitly, never read
  from the system clock. An invalid token never yields an `AccessContext`.
- The entity holds only the SHA-256 digest, as a `TokenHash`: a validated, immutable non-dataclass whose `repr`,
  `str` and `format` are `<redacted>`, so `repr(token)`, f-strings, `dataclasses.asdict`/`astuple`, `copy` and
  logging never show the hex (`asdict` falls back to `deepcopy`, which returns the same redacted object; pickling
  is refused). The hex is read only through the explicit `.hexdigest` (stores that persist it). `token_hash` is
  **outside `AgentToken.__eq__`/`__hash__`**: two tokens differing only in hash are equal, so store tests assert
  the digest explicitly. `TokenHash` equality is constant-time; `TokenStore.find_by_hash` takes a `TokenHash`.
  The plaintext secret never enters the domain.
- Stored hashed (SHA-256; tokens are high-entropy random). Shown once at creation.
- The token name becomes `source.agent` on proposals.
