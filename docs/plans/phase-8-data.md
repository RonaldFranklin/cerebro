# Phase 8: new data for the redesigned screens

**Status: proposal, awaiting the owner.** Nothing here is implemented. The screens ship with the data that exists
today; each part below starts only after the owner approves it. Parts marked **ADR** get one before the code.

## Overview

| Part | Screen | New data or rule | Review |
|---|---|---|---|
| A. Health summary | Health | Content revision, note count, check time, listener ceilings | Security (listener configuration in a response) |
| B. Last backup | Health | A status file written by the backup job | Security (new file read), deploy docs |
| C. Token table | Health | Token list, last use, 24-hour activity | **ADR**, security |
| D. Live validation | Edit | A dry-run check of a draft | Security (secret scanner over a new route) |
| E. Commit message | Edit | An owner message in the commit | **ADR** (commit format), security |
| F. Note history | Note | Changes of one note | Security |
| G. Superseded changes | Recent changes | A `superseded` operation | Small |

All new reads require the owner with ceiling `restricted`, like Health today. On the public listener they therefore
exist only with `--public-browser-ceiling restricted`. None is reachable through `/mcp`.

## A. Health summary

`GET /api/health` also returns:

| Field | Source |
|---|---|
| `checked_at` | server clock |
| `content.revision` | repository `head()` |
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

Columns: client (agent name), ceiling, scopes, last used, activity over 24 hours. Active tokens first; revoked and
expired ones in a collapsed group. Token ids, secrets and hashes are never sent.

What is counted: one count per **successfully authenticated MCP request**, whatever its method. Stored: token id, UTC
hour, count; and the time of the last use. No method, tool name, note id, argument, address or body. Rows older than
25 hours are deleted on write. Refused credentials are not counted. A failure to count never fails or delays
authentication beyond the database busy timeout; it is dropped.

**Decision needed: where the counters live.** `state.sqlite` accepts only its exact schema version and is never
upgraded (ADR-0023), so adding tables to it today would stop the production service at startup.

| Option | Effect |
|---|---|
| **C1 (recommended): `usage.sqlite` in the state directory** | No change to the token database. Not backed up; losing it loses only counters. A rollback is unaffected. |
| C2: tables in `state.sqlite` | Needs a first in-place upgrade (schema 5 to 6) and an ADR amending ADR-0023. An older release then refuses the file, so a rollback needs a restored backup. |

You asked for counters in the state database. C1 keeps them in the state directory but in a separate file; choose C2
if they must be in `state.sqlite` itself.

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

**Decision needed: broken links.** Owner edits only warn about them; agent writes refuse them. Your request is "the
same checks as agent writes" with saving blocked while any fails.

| Option | Effect |
|---|---|
| **D1 (recommended):** the screen blocks on any failed check, including broken links; the save API keeps the owner policy | The screen is strict; no domain rule changes. Fixing an unrelated old broken link is required before saving that note in the browser. |
| D2: owner edits adopt the agent policy in the domain | Enforced everywhere. Changes the domain model and needs an ADR. |

## E. Commit message

History recognizes a commit only when its message is exactly `Update note: <id>`, a blank line and
`Written-by: owner`. Free text in the subject would hide the change from Recent changes.

Proposal: keep the subject and add the owner's message as one optional line in its own paragraph:

```text
Update note: <id>

Message: <owner text>

Written-by: owner
```

The domain accepts one line of at most 100 characters without control or separator characters, so it cannot add a
trailer. It is refused when the secret scanner matches or when it holds a wikilink. The history parser accepts exactly
this optional line, for the owner only. Git history is readable by whoever holds the content repository, including
the backup remote: **the message is stored in plaintext even for a restricted note**, and the field says so. Recent
changes shows it only to a session that may see every version in the diff. Needs an ADR amending ADR-0020.

## F. Note history

`GET /api/notes/{id}/changes` lists the attributed changes of one note: operation, writer, time and revision, with the
same authorization and redaction as Recent changes. It searches the same bounded history window and pages the same
way, so an old note may show "no changes in the recent window" with a way to load older ones.

## G. Superseded changes

A change whose new version has status `superseded` and whose previous version did not is reported as `superseded`.
It is derived from the two versions the history already authorizes; nothing new is stored.

## Not planned

- A list of authors from the token table: the Recent changes filter offers the authors present in the loaded changes.
- Sparklines of anything other than request counts.

## Suggested order

A and G, then F, B, C, D and E.
