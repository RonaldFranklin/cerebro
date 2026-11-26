# ADR-0021: Readable agent-created note IDs and default write quota

- **Status:** accepted
- **Date:** 2026-11-26
- **Supersedes in part:** [ADR-0020](0020-direct-agent-writes.md), for new note IDs and the default quota.

## Context

The owner wants IDs that are recognizable in wikilinks, paths and Git history, and a higher default write allowance.

## Decision

For each agent create, derive an ASCII kebab-case stem of up to 60 characters from the title, using `note` when no ASCII word remains. Append a hyphen and four random lowercase ASCII letters or digits. Resolve collisions under the writer lock by changing the suffix. After 32 consecutive collisions, return a retryable error. Keep the original ID on title edits and on replay of an identical create.

Set the default to 60 attempts per token per rolling hour. Existing TOML and environment overrides retain their values.

## Consequences

Existing IDs and wikilinks need no migration. For restricted notes, the title-derived ID is visible in filenames and Git metadata even though the note payload is encrypted. Use titles suitable for that exposure.

## Alternatives considered

Keeping opaque IDs would make wikilinks hard to recognize. A title alone cannot distinguish notes with identical normalized titles.
