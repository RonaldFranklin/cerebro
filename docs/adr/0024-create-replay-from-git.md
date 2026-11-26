# ADR-0024: Detect create replays from the committed note

- **Status:** accepted
- **Date:** 2026-11-26
- **Amends:** the replay binding of [ADR-0020](0020-direct-agent-writes.md) ("bind it durably to the committed
  create and a canonical fingerprint of the validated request").

## Context

ADR-0020 lets an agent repeat a create after a lost response: the same token and `request_id` with the same input
returns the original note and revision, and different input returns `request_conflict`. The implementation sealed a
fingerprint of the canonical request in an encrypted `Write-Receipt` trailer, bound with the Fernet key to the
commit subject, actor and request key. Reading it needed a commit-message parser, a binding format, base64
handling and the cipher in the repository's commit path.

The create commit already holds everything the comparison needs: the note as it was committed, stored in plaintext
for public and internal notes and as ciphertext for restricted ones.

## Decision

- An agent create commit is `Add note: <id>`, a blank line, `Written-by: <agent>` and
  `Write-Request: <sha256(token id, request id)>`. There is no `Write-Receipt` trailer and no fingerprint.
- `NoteRepository.find_create(request_key)` returns the commit carrying that key with the note read from that
  commit (`CommittedCreate`). Any malformed message, missing or unreadable note fails closed with `NoteWriteError`.
- The use case keeps the order of ADR-0020 and ADR-0022: the current stored note must be visible and pass the
  core-scope check, then the candidate is parsed with the original id and checked against the ceiling, the
  core scope and reference visibility. Only then is it compared with the committed note, ignoring `created`
  and `updated`. Equal returns the original id and revision; different returns `request_conflict`.
- The request key stays bound to the authenticated token id, so another token's request id never matches.
  Hidden and missing notes still get the same `target_unavailable`, so a replay is no oracle. Nothing new is
  written to git, so restricted content stays encrypted in history.

## Consequences

**Guarantee lost.** The sealed receipt was authenticated with the encryption key: someone who could rewrite the
content repository's history but did not hold the key could not forge or alter a receipt without detection.
The plain `Write-Request` trailer and its `Written-by` line are now trusted like the rest of git history. Whoever
can rewrite that history can forge a replay record, relabel the actor of a create or make a replay match edited
content. Restricted note bodies remain authenticated by Fernet as before. This matches the existing trust model:
write access to the content repository already lets someone change public and internal notes and every
`Written-by` trailer, and the [accepted risks](../architecture.md#accepted-risks-owner-decision-2026-11-25) already
rely on the owner protecting it.

The repository loses the receipt format, its cipher use in commits and the fingerprint field of
`AgentWriteCommit`. A replay costs one extra historical blob read. There is no compatibility path for receipts
written by earlier releases ([ADR-0023](0023-no-legacy-compatibility.md)).

## Alternatives considered

- **Keep the encrypted receipt:** authenticates one trailer of a history that is otherwise unauthenticated, at
  the cost of a binding format, parser and tests.
- **A plaintext fingerprint trailer:** a hash of low-entropy content could confirm guesses of a restricted note.
- **Store replay records in the state database:** a second source of truth that a restore could separate from
  git.
