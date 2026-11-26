# ADR-0026: Isolate invalid committed notes on writes

- **Status:** accepted
- **Date:** 2026-11-26
- **Amends:** [ADR-0020](0020-direct-agent-writes.md) for write admission and validation when stored files are invalid.

## Context

One malformed file in Git blocked every write and consumed a quota slot on each attempt. The owner wants to
continue writing independent notes and reserve the quota for requests the agent can correct or complete.

## Decision

Validate against usable notes in the current committed revision. Stable exclusions block direct updates and
links to those ids. The owner can inspect and repair them using Health and Git. Unstable scans still block writes.
Count usable current P0 notes for the [combined word budget](0025-core-word-budget.md).

Keep the reservation for successful writes, including a committed write followed by an indexing failure.
Return the reservation for server failures, including rejection of a target or references solely because their
committed files are invalid. Missing and hidden notes still cost the same and return identical feedback.

## Consequences

A malformed file contains no readable note. Its rejection uses the same feedback as a missing or hidden id, but
quota exhaustion can distinguish a malformed file. Repairing a P0 directly in Git can change the total usable word
count. Health checks the total at the repaired revision.

## Alternatives considered

Charging malformed targets consumes quota for a condition only the owner can fix. Refunding every unavailable
target when a malformed file exists would exempt unrelated agent errors from the limit.
