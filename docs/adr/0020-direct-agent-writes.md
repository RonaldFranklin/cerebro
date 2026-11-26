# ADR-0020: Validated direct agent writes and owner change history

- **Status:** accepted; ID generation and the default quota are superseded by [ADR-0021](0021-readable-note-ids.md);
  authority over core and P0 notes is amended by [ADR-0022](0022-core-scope.md); its preservation of legacy
  tokens, proposal rows, state upgrades and Inbox-era attribution is superseded by
  [ADR-0023](0023-no-legacy-compatibility.md)
- **Date:** 2026-11-26

## Context

The owner wants agents to record decisions and tested procedures during the session that produced them.
The Phase 4 Inbox requires a separate owner action for each change. The owner authorized direct writes
and review through Git history.

## Decision

- Issue a distinct `write` scope explicitly. Preserve legacy tokens and read grants on upgrade. A
  legacy `propose` grant confers no direct-write authority. Gate discovery and invocation on scope.
  Give agents that need to search and update both `read` and `write`.
- Keep the four read tools. Replace `cerebro_propose` with `cerebro_write` (create or update) and
  publish a short, versioned English guide through `cerebro_write_guide` on demand. Both require
  `write`. The application obtains the agent and quota token id from authentication. Reject caller
  attribution. Each accepted content change produces one Git commit with a validated `Written-by`
  trailer carrying the agent name. Retain attribution on historical and owner commits.
- Serialize validation, compare-and-swap, commit and index maintenance with other writers. Use the
  lesser of the socket and token ceilings on targets, candidates and references. Allocate unpredictable
  note ids on create and resolve collisions internally. A missing and an invisible update target yield
  the same error. Only a complete, unredacted read of a writable projection supplies an update
  `base_hash`. Recheck visibility and the full hash under the lock. Keep immutable note fields intact.
- Run schema, per-note and combined budgets, link and secret checks before committing. Check inbound
  links on sensitivity changes. Return bounded field codes and correction hints, including stale-base
  recovery. Project findings to the requester: never echo raw input, hidden target ids, counts from
  hidden notes, hashes of hidden content, or raw scanner matches. Hidden and missing references receive
  the same rejection and remediation, including references inside Markdown code or an alias. Check
  every target covered by read-time redaction before evaluating target-dependent rules. Preserve existing warning and error severity for agent note validation.
- Use a durable rolling-hour limit per authenticated token id (default 20, configurable). Count admitted
  attempts including invalid candidates and conflicts, so a failing loop also stops. Atomically reserve
  before application validation and keep only quota metadata. A refusal at capacity records nothing.
  A crash may consume a slot. Create requests carry a client-generated request id scoped to the token.
  Bind it durably to the committed create and a canonical fingerprint of the validated request.
  Identical replay after a lost response returns the existing receipt. Reuse with different input
  returns a bounded conflict without writing.
  A replay must pass current authorization and cannot reveal a now-hidden note.
  A committed write remains authoritative if a derived index update fails; recover using the recorded
  index revision and Git HEAD.
- Replace operational Inbox review with a private owner **Recent changes** view from Git. A bounded
  history port returns note, actor, time and diff. Include legacy attributable edits. Restrict both list
  and detail to the authenticated private owner, authorize the current note and both historical revisions,
  validate revision identifiers, and fail closed on unreadable history. Render bounded escaped diffs of
  authorized note content, including encrypted notes through the existing cipher. Preserve historical
  commits, sealed proposal rows, token records and backups. Retire pending review operations at cutover.
- Supersedes ADR-0006, ADR-0007, ADR-0016 and ADR-0018. Amends the tool inventory in ADR-0014 and
  replaces the Inbox surface in ADR-0019. Their unrelated read, transport and browser controls still apply.

## Consequences

An agent can correct rejected input immediately. Content quality now also depends on agents following
the guide, searching before creating, and preferring updates. The owner can inspect or recover a committed
version using existing Git history and backups. Quota reservations and Git commits have separate durability
boundaries. Deployment must preserve the state database, content repository and encryption key together.

## Alternatives considered

Keeping an external curator or owner approval would retain a pending queue and delay each write.
Silently translating existing proposal tokens to write tokens would grant more authority to credentials
already distributed. A client-selected id on create would let an agent test whether an invisible id exists.
