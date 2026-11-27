# Phase 8: new data for the redesigned screens

**Status: approved by the owner on 2026-11-27**, with the decisions recorded below. Each part starts in the approved
order. Parts marked **ADR** get one, accepted by the owner, before the code.

## Overview

| Part | Screen | New data or rule | Review |
|---|---|---|---|
| A. Health summary | Health | Content revision, note count, check time, listener ceilings | Security (listener configuration in a response) |
| B. Last backup | Health | A status file written by the backup job | Security (new file read), deploy docs |
| C. Token table | Health | Token list, last use, 24-hour activity | **ADR** (live channel, step 4), security |
| D. Live validation | Edit | A dry-run check of a draft | Security (secret scanner over a new route) |
| F. Note history | Note | Changes of one note | Security |
| G. Superseded changes | Recent changes | A `superseded` operation | Small |

All new reads require the owner with ceiling `restricted`, like Health today. On the public listener they therefore
exist only with `--public-browser-ceiling restricted`. None is reachable through `/mcp`.

## A. Health summary

`GET /api/health` also returns:

| Field | Source |
|---|---|
| `checked_at` | server clock |
| `content.revision` | repository `head()` after a successful scan; empty otherwise |
| `content.state` | `clean`, or `attention` when the report lists excluded or blocking note files |
| `listeners` | for each started listener: `name` (`private`, `public`), MCP ceiling, browser ceiling |

Quarantine and index state come from the findings already returned. The listener data is static configuration passed
in by the composition root. It holds no origin, host name, address or port, and says nothing about an identity layer,
which Cérebro cannot observe. The "all systems normal" label means no error and no warning.

## B. Last backup

- After a snapshot is complete **and** the content push succeeded, `deploy/backup.py` atomically writes
  `<state>/last-backup.json` (mode `0600`): `{"completed_at": "<UTC ISO time>", "content_revision": "<hex>"}`.
- A new read port returns that record. The adapter opens a regular file without following links, reads a small bounded
  size and accepts only those two fields. A damaged file shows as "unreadable", an absent one as "never recorded".
- Health shows the date, in amber when older than 48 hours or never recorded.
- Limits: it reports what the job wrote. It does not prove that a restore works, and it does not see failures of
  copies made outside this script. A failed backup leaves the earlier record, which the age warning exposes.
- The file is not part of the backup itself.

## C. Token table, last use and activity

Columns: client (agent name), ceiling, scopes, last used, activity over 24 hours. Active tokens first; revoked ones
in a collapsed group. An "expired" group appears only when an expired token exists: the owner's tokens do not expire
(one per agent, valid until revoked). Token issuing and validation do not change. Token ids, secrets and hashes are
never sent.

What is counted: one count per **successfully authenticated MCP request**, whatever its method. Stored: token id, UTC
hour, count; and the time of the last use. No method, tool name, note id, argument, address or body. Rows older than
25 hours are deleted on write. Refused credentials are not counted. A failure to count never fails or delays
authentication beyond the database busy timeout; it is dropped.

**Decided (C1):** the counters live in `usage.sqlite` in the state directory. The token database, `state.sqlite`, is
unchanged: it accepts only its exact schema version and is never upgraded (ADR-0023). The new file is not backed up,
losing it loses only counters, and a rollback is unaffected.

### Shared design with the live channel (step 4)

The counters and the live synapses observe the same traffic, so one event is designed for both before either is
built. The ADR for the live channel (SSE) is proposed with this part and accepted before implementation. It starts
from these limits:

| | Usage counter (part C) | Live event (step 4) |
|---|---|---|
| Raised when | an MCP request authenticates | an agent has read or written a note successfully |
| Content | token id, time | note id, verb (`read` or `write`), client name, time |
| Kept | hourly counts and last use, in `usage.sqlite` | in memory only, never written to disk |
| Seen by | the owner, through Health | the owner's browser session, filtered by its ceiling: a note above it produces nothing |

Neither carries note content, arguments, addresses or credentials, and neither may fail or slow an agent request.
The ADR also fixes the connection bound, what happens when the session ends, and the behaviour under
`prefers-reduced-motion`.

## D. Live validation

`POST /api/notes/{id}/validate` takes the same body as the save, with the same session, Origin, CSRF, size and JSON
limits, and writes nothing. It returns:

| Check | Content |
|---|---|
| Schema | field violations |
| Word budget | word count and the soft and hard limits **of that note's type** (hubs and core notes differ from the 1,500/3,000 of other notes) |
| Links | violations with line numbers, never the target of a hidden note |
| Sensitivity | links to higher sensitivity |
| Secret scan | field and line only, never the matched text |
| Base version | whether the stored hash still equals `expected_hash` |

The browser calls it after a pause in typing, cancels the previous call and never logs the draft.

**Decided (D1):** the screen blocks saving while any check fails, broken links included. The save API keeps the owner
policy, so no domain rule changes. A note with an old broken link must have it fixed before it can be saved in the
browser. The mockup's commit message field is not built.

## F. Note history

`GET /api/notes/{id}/changes` lists the attributed changes of one note: operation, writer, time and revision, with the
same authorization and redaction as Recent changes. It searches the same bounded history window and pages the same
way, so an old note may show "no changes in the recent window" with a way to load older ones.

## G. Superseded changes

A change whose new version has status `superseded` and whose previous version did not is reported as `superseded`.
It is derived from the two versions the history already authorizes; nothing new is stored.

## Not planned

- **E. Commit message** (a field in the editor whose text goes into the commit). Dropped by the owner on 2026-11-27:
  it would need an ADR for the commit format and would keep plaintext in Git history even for restricted notes, which
  is not worth it for a single owner. Commits keep their fixed subject and `Written-by` trailer.
- A list of authors from the token table: the Recent changes filter offers the authors present in the loaded changes.
- Sparklines of anything other than request counts.

## Order

A and G, then F, B, C and D. Step 4 follows the design shared with C.
