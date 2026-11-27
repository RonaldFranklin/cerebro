# ADR-0030: The backup job leaves a status record that Health reads

- **Status:** proposed
- **Date:** 2026-11-27

## Context

The owner wants Health to show when the instance was last backed up. The backup runs on the host, outside the service
container ([Phase 6](../plans/archive/phase-6.md)), and the service has no view of its schedule, snapshots or remote.
Both can reach the state directory.

## Decision

- After a snapshot is complete **and** the content push succeeded, `deploy/backup.py` replaces
  `<state>/last-backup.json` (mode `0600`) with `{"completed_at": <UTC ISO time>, "content_revision": <hex>}`, by
  renaming a new file over it. Failing to write it is reported and does not turn a completed backup into a failure.
- A new application port, `BackupStatus`, returns `recorded`, `never` or `unreadable`. Its adapter opens a regular
  file without following links, reads at most 512 bytes and accepts exactly those two fields.
- Only the owner's browser Health response carries the record. `cerebro health`, `/mcp` and the backup contents do
  not.
- The screen warns when the record is older than 48 hours, never made or unreadable. It does not change the report's
  findings or overall status.

## Consequences

- The record is the job's own account. It does not prove that a restore works, and it does not see copies made by
  other tools. A failed backup leaves the earlier record, which the age warning exposes.
- Whoever can write the state directory can forge it; they already control the token database beside it.
- An instance without this job always shows "never recorded".
- The record appears only after the host runs the updated script from the deployed release.

## Alternatives considered

- A row in `state.sqlite`: the host script would write the live token database, and a new table needs a schema
  upgrade (ADR-0023).
- Mount the backup directory into the container: it exposes every snapshot, with token hashes and the password
  verifier, to the service.
- Ask GitHub for the branch tip: network access and credentials in the service, and no view of the state snapshot.
