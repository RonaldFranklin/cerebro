# ADR-0011: Single-user instances

- **Status:** accepted
- **Date:** 2026-11-24

## Context

Each instance belongs to whoever runs it. There is no need for multiple human users.

## Decision

- One owner per instance. The web UI has a single admin login (password hashed with `hashlib.scrypt`,
  secure HTTP-only session cookie).
- Agents authenticate with per-agent bearer tokens (ADR-0005, domain model §8).
- No user management, roles or sharing.

## Consequences

- Much simpler auth and data model.
- Multi-user support, if ever needed, would require a new ADR.

## Alternatives considered

- Delegating UI auth to Tailscale identity only: convenient, but ties the app to one network provider.
