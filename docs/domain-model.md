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
  `warning` (reported, e.g. by the health report, but not blocking). Agent writes and owner edits share the same
  rules; today both treat only the soft word limit and duplicate `related` ids as warnings; **broken links are
  additionally a warning for owner edits** and an error for agent writes. Structural frontmatter problems are
  always errors. An agent create may also return a `possible_duplicate` warning: the words of its title and
  summary overlap at least by half (Jaccard similarity; bodies are not compared) with those of a current note the
  caller may see. Only for a token with `read` does the warning name that note; it never names one above the
  caller's ceiling.
- Enforced outside the domain: `id` equals the file name (filesystem repository adapter, Phase 2); `id` is
  immutable on update.
- **Cross-note rules** are pure functions over a snapshot `Mapping[NoteId, NoteRef]` (see Links); the proposed
  note's `NoteRef` replaces the stored one when validating a change. They report only problems that involve the
  validated note; a health report runs them for every note.

### Links

- Body links use wikilinks: `[[note-id]]` or `[[note-id#Section heading]]`.
- The domain extracts links from the body plus `hub`, `related` and `superseded_by` to build the graph.
- Links inside fenced code blocks and inline code are examples and are ignored; so are targets that are not
  valid note ids. The `[[id|alias]]` form is not supported. Duplicate links are collapsed.
- A link (body, `related`, `superseded_by`) to a missing id is a **broken link** (`broken_link`: warning for
  owner edits, error for agent writes). Section anchors are not checked against the target (that needs its body).
- **No link to a higher sensitivity.** A note may not link to a note of **higher** sensitivity than its own, through
  body wikilinks, `hub`, `related` or `superseded_by` (`link_to_higher_sensitivity`). Bodies are served as written,
  so such a link would hand the id of a restricted note to every reader whose ceiling covers the source but not the
  target. It is an **error on every write path**: unlike `broken_link` it is never a warning, not even for owner
  edits (it is in no severity policy's warning set). The body check covers every `[[id]]` that read-time redaction
  treats as a link, including inside code spans and blocks and the `[[id|alias]]` form. The rule also runs
  **inbound**: when a note is created or its sensitivity rises (agent writes and owner edits alike), stored notes of lower sensitivity that link to it
  (body, `hub`, `related`, `superseded_by`) are errors, reported as "<linking-id> links to this note". Equal or lower sensitivity is fine. A missing target is a
  `broken_link`, not this. The message names only the target id as the author wrote it (never its title or
  sensitivity); only owner diagnostics may include details about other stored notes. For direct agent
  writes, reject a missing or invisible target identically for every reference covered by read-time
  redaction, including code and aliases. Check these before target-dependent rules. The health report runs
  the rule too, so existing leaks are listed as errors.
- `title` and `summary` may not contain `[[` (`wikilink_in_metadata`, error on every write path): search results
  and the overview serve them without link redaction.
- A `hub` that does not exist (`hub_not_found`) or whose type is not `hub` (`hub_not_a_hub`) is always an error.
- `superseded_by` chains that lead back to the note are a `superseded_by_cycle` error.
- **Redaction on read (defense in depth).** `ReadNote` replaces every `[[...]]` in the served body (whole note or one
  section) whose target is a note id the reader cannot see (above the effective ceiling, or missing) with the
  constant `[[redacted link]]` (`cerebro.domain.links.REDACTED_LINK`): the whole link goes, anchor and alias
  included, and code blocks are covered too. Links to `superseded` or `archived` notes within the ceiling are kept,
  so a note that points to history stays complete and updatable. Brackets that are not note ids are left alone.
  `hub`, `related` and `superseded_by` of the `NoteView` are filtered by the same rule, and hub `members` to visible
  targets. `GetOverview` redacts the P0 bodies it serves in the same way, keeping links to readable history, while it
  still serves only current P0 notes. This protects against data that predates
  the rule or was edited outside Cérebro. The marker's text is not a valid note id, so it is never a link.
- Escaped or entity forms (`\[\[x]]`, `&#91;&#91;x]]`) are plain text, not links: neither the rules nor redaction treat
  them as such, and the UI must not render them as links.
- `NoteRef` is the body-free view used by these rules: id, title, type, priority, sensitivity, status, `hub`,
  `related`, `superseded_by` and the wikilinks already extracted from the body.
- **Sections.** `ReadNote` reads one section by heading: ATX headings (`#` to `######`), matched ignoring case
  and extra whitespace, first match wins; the section includes its sub-sections and ends at the next heading of
  the same or a higher level. Headings in fenced code blocks do not count. Sections are taken from the redacted body.
  Asking a readable note for a heading it lacks lists the available ones (bounded); a hidden note and a missing note
  fail identically, with or without a section.

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
| P0 | Core: global rules and facts every agent must know | always, by `GetOverview` | **all P0 together ≤ 800 words** |
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

- **Listener ceiling:** private listener (Tailscale) = `restricted`; public listener (Cloudflare) = configured,
  default `internal` ([ADR-0028](adr/0028-token-decides-public-access.md)). The deployment sets `restricted` so the
  token ceiling decides for agents. The listener ceiling above applies to tokens. The signed-in owner's browser
  is `restricted` on the private listener. On the public one it is capped at `internal` unless the owner opts in to
  `restricted` with `--public-browser-ceiling` ([ADR-0029](adr/0029-owner-browser-full-access-on-public-listener.md)).
- **Token ceiling:** set when the owner issues the agent token.
- Notes above the effective ceiling are **invisible**: not in search, not in hubs, not in neighbors, not in
  the graph. Reading one by id returns "not found" (no existence leak).
- **Visibility rule** (`cerebro.domain.access.is_visible`): a note is visible when its sensitivity is `<=` the
  effective ceiling and its status is current, or `include_history` is set (`superseded` / `archived`).
  `include_history` never raises the ceiling. Callers treat "not visible" exactly like "does not exist".
- **Reading by id includes history.** `ReadNote` serves a `superseded` or `archived` note within the ceiling, with
  its `status` in the view, and keeps its links and references to such notes; `include_history` on a read only adds
  history members to a hub's list. Search, the overview, hub lists, neighbors and the graph keep excluding history
  by default. A note above the ceiling stays "not found", whatever its status.
- **Fail closed:** `effective_ceiling(listener, token)` has no defaults; wrongly typed ceilings raise, and
  `is_visible` / `AccessCeiling.allows` / `AccessContext.can` deny any value of an unexpected type.
- **Principals and scopes:** `AccessContext(principal, ceiling, scopes)` is built once per request, with
  `ceiling` already the effective ceiling. `principal` is `Agent(name)` or `Owner`. Scopes are `read`,
  `write` and `core`. `core` extends `write` to core and P0 notes and is refused on a credential without
  `write` ([ADR-0022](adr/0022-core-scope.md)). The **owner has every scope** but is still limited by the **listener** ceiling (there is no token);
  owner-only actions (owner edit, change history, maintenance) require the `Owner` principal, not a scope. An agent only
  has the scopes of its token. Agent names are lowercase (`claude-code@workstation`, at most 64 characters);
  `owner` (with or without a host part) is reserved so an agent can never appear as the owner in trailers.
- `restricted` notes are encrypted at rest in the content repository ([ADR-0008](adr/0008-encryption-of-restricted-notes.md)).

### Raising a note to `restricted`: previous versions stay in history

Every applied change is a git commit and history is never rewritten. When an **existing** note's sensitivity
rises to `restricted` from `public` or `internal`, the successful write returns a fixed warning about
previous versions in git and backups. The new level protects the new revision. Scrubbing history and
backups is a manual owner operation.

## 5. Status

| Status | Meaning | Served as current? |
|---|---|---|
| `current` | Valid knowledge (non-operational) | yes |
| `proposed` | Planned change/config, not done yet | yes, clearly labeled |
| `applied` | Done, not verified | yes, labeled |
| `verified` | Done and tested | yes |
| `superseded` | Replaced; `superseded_by` required | **no** — only with `include_history`, or read by id |
| `archived` | Kept for history only | **no** — only with `include_history`, or read by id |

This encodes two rules: *prefer the current state over history*, and *distinguish planned, applied and
tested*.

## 6. Secrets

Notes must never contain secrets. They may say **where** a secret lives (e.g. "password manager, item X"),
never its value. The domain has a secret scanner (`cerebro.domain.secrets`, pure standard library) that
`validate_note` runs on every agent write and every owner edit. A hit is a `secret_detected` violation, an
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
  wikilinks are never flagged. Neither are note ids (ids from long titles with digits look random): the note's own
  id and, in any field or the body, a token that is exactly the id of a note the writer may read (the health report
  reads every note). The other detectors still apply to ids, and an id of a note above the writer's ceiling is
  scanned like any token, so the result does not reveal that the note exists.

**Reports never contain the secret.** A finding carries the detector kind, the field, the line number and safe detector metadata. Thresholds are constants in the module. `parse_note`
reports a rejected frontmatter value (or unknown key) that the scanner flags as `secret_detected` instead of
quoting it.

**Errors and report messages never contain a received value.** Exceptions from constructors and parsers
(`NoteId("...")`, `Priority.parse(...)`), application errors (`NoteNotFound`, ...) and
`Violation`/`Finding` messages name only the field and, where useful, the type or length of what was received.
Allowed-value lists are fine. Exactly what is and is not named:

- Never named: received values, unknown frontmatter keys (reported as `unknown field #N`, with length, N being
  the 1-based order of appearance), link, `related`, `hub` and `superseded_by` targets (reported by position:
  `link #N of body`, `related entry #N`), and any id on a `superseded_by` or hub chain (reported by length:
  `4 hubs deep (maximum 3)`), because chains walk the unfiltered snapshot.
- Named, by exception: the id of a note similar to a new one, in the `possible_duplicate` warning, only to a
  token with `read` and within its ceiling (see above); the id of a *stored* note that links to the one being checked, in the inbound-link
  sensitivity finding (`<id> links to this note`). That note is by definition below the checked note's
  sensitivity, so naming it reveals nothing the reader could not see; and health-issue subjects (stored note ids, and the paths of problem note files with a fixed reason, in the owner-only health report).
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
integrity hashes) and SSH public keys; the owner then rewords the note. The note-id exemption covers only a token
that is exactly an id: an id glued to `/`, `=`, `+` or `_` (for example `notes/<id>` or `id=<id>`) forms a longer
token and can still be flagged. Because the note's own id is exempt, a secret split across title words (each too
short to flag) reappears hyphenated in a generated id without being flagged there; the title scan has the same blind
spot. An agent must omit secret values before submitting a note.

## 7. Agent writes

Agent creates and updates are synchronous: each is validated and committed, or refused
([ADR-0020](adr/0020-direct-agent-writes.md)).

| Operation | Preconditions | Result |
|---|---|---|
| Create | Authenticated agent with explicit `write` scope, valid note within ceiling | One new note with a service-generated id derived from the title. |
| Update | Visible, fully readable note and the `base_hash` of the version the agent read | One replacement of the same note, retaining its id and creation date. A stale hash requires a fresh read and reconciliation. |

Core and P0 notes are the global rules the overview serves to every agent. An agent without the `core`
scope gets `core_scope_required` when the stored version **or** the candidate has type `core` or priority
`P0`: it cannot create one, edit, archive, supersede or demote one, or promote a note to one. A create
replay is checked against the candidate and the stored note in the same way. Hubs (P1) stay open to every
`write` token. With `core`, the category rule (a note keeps its core, hub or regular type) and the combined
800-word P0 budget still apply.

On create, the service converts the title to an ASCII kebab-case stem of up to 60 characters, using
`note` if the stem is empty. It appends a hyphen and four random characters from `a-z` and `0-9`,
and retries collisions internally. Existing ids stay valid. Changing a title preserves the id.
An identical create replay with the same token and `request_id` returns the original note id and revision
after authorization. The service compares the candidate with the note in the create commit, ignoring `created`
and `updated`; any other difference returns `request_conflict` without writing
([ADR-0024](adr/0024-create-replay-from-git.md)). Use non-sensitive titles because restricted filenames and Git history can reveal title words.

The application issues an update hash only with an eligible full read. A section or a representation
with redacted body links or metadata cannot be used as the basis for a full-note replacement. The writer
checks visibility of the target and candidate, projection eligibility and the expected hash again under the
writer lock. Hidden and missing targets have the same external response. Agents cannot supply an author or
commit message. Git records the authenticated agent on each successful write.

Validation applies the controlled vocabulary, schema, hard word and overview budgets, link and sensitivity
rules and secret scan to the entire candidate. Each token allows 60 admitted write attempts per rolling hour by default. Configure the limit
with `writes_per_hour` in TOML or the `CEREBRO_WRITES_PER_HOUR` environment override. The count spans sessions,
processes and restarts to stop repeated invalid requests as well as runaway successful writes. Server failures
return the reserved slot, including refusals caused solely by an invalid committed file.
Failures return bounded field names, safe codes and correction hints. An unavailable reference names its field
and position in the submitted note (related entry or text line), identically for hidden and missing targets;
every `[[note-id]]` counts, code included. The service never echoes a submitted secret or identifies an
inaccessible note. The write guide response also lists the repository's `areas` and `types`, as loaded for
validation; it holds schema names only and requires the `write` scope. A rejected or stale write produces no note, git
commit or pending item. Owner edits use the owner's access context and the existing owner severity policy.

The content hash is the SHA-256 hex digest of the UTF-8 JSON object
`{"frontmatter": note_to_raw(note), "body": body}` with compact separators, canonical key order,
literal Unicode, ISO dates and absent optionals omitted. The repository compares
the expected hash during apply. A concurrent update against the same base can succeed only once.
A committed change is durable even if a derived index needs rebuilding.

The owner's Recent changes view reads git history. It shows the authenticated writer, time, note
and bounded diff. Exposing a change requires visibility of the current note and all note versions represented
in the diff, including the parent version for updates. Historical data receives the same link redaction as
current content. Only commits with a valid `Written-by` trailer are listed: an agent name, or `owner` for an
owner edit, which always updates an existing note ([ADR-0023](adr/0023-no-legacy-compatibility.md)). Any other
commit is not attributed.

## 8. Agent tokens

- One token per agent (e.g. `claude-code@workstation`, `codex@workstation`).
- Attributes: id, agent name, newly issued scopes (`read`, `write`, `core`; at least one, and `core` only with `write`), ceiling, created, optional expiry,
  optional revocation time, token hash. (`last_used` is operational data kept by the token store, not part of the
  domain entity.)
- A token is valid when it is not revoked and `now < expires_at`; `now` is always passed explicitly, never read
  from the system clock. A token created more than five minutes ahead of the verifier clock also fails
  authentication. An invalid token never yields an `AccessContext`.
- The entity holds only the SHA-256 digest, as a `TokenHash`: a validated, immutable non-dataclass whose `repr`,
  `str` and `format` are `<redacted>`, so `repr(token)`, f-strings, `dataclasses.asdict`/`astuple`, `copy` and
  logging never show the hex (`asdict` falls back to `deepcopy`, which returns the same redacted object; pickling
  is refused). The hex is read only through the explicit `.hexdigest` (stores that persist it). `token_hash` is
  **outside `AgentToken.__eq__`/`__hash__`**: two tokens differing only in hash are equal, so store tests assert
  the digest explicitly. `TokenHash` equality is constant-time; `TokenStore.find_by_hash` takes a `TokenHash`.
  The plaintext secret never enters the domain.
- Stored hashed (SHA-256; tokens are high-entropy random). Shown once at creation.
- The authenticated token name supplies direct-write attribution.
