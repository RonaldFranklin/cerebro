# ADR-0008: Encrypt restricted notes at rest with Fernet

- **Status:** accepted
- **Date:** 2026-11-24

## Context

The private content repository is pushed to an external remote as backup. Restricted notes (e.g. network and
access details) should not be readable there. The owner wants simple, light protection, not a high-assurance
scheme.

## Decision

- Use Fernet (symmetric, authenticated) from the `cryptography` library, behind the `Cipher` port.
- A restricted note file keeps a minimal plaintext header and encrypts everything else, including title and
  summary:

```markdown
---
id: server-ssh-access
sensitivity: restricted
encryption: fernet-v1
---
gAAAAAB...   (Fernet token of the full original note: frontmatter + body)
```

- One key per instance, stored in the server's secrets directory, never in git.
- The owner keeps an offline copy of the key (e.g. password manager). **Losing the key means losing restricted
  notes, including backups.**
- Decryption happens in memory on the server for indexing and serving through the private listener.
- Key rotation: a CLI command re-encrypts all restricted notes (Fernet `MultiFernet`).

## Consequences

- The remote only sees ids of restricted notes.
- The derived index on the server contains plaintext and must not leave the server.
- Diffs of restricted notes in git are opaque; history is still available after decryption.

## Alternatives considered

- `git-crypt`: transparent, but adds a tool for every clone and encrypts whole files including ids.
- SOPS / age: solid, but more moving parts than needed for this level of protection.
