# ADR-0005: Sensitivity levels and access ceiling by listener and token

- **Status:** accepted, amended by [ADR-0028](0028-token-decides-public-access.md) (configurable public ceiling)
- **Date:** 2026-11-24

## Context

Knowledge ranges from public study notes to infrastructure access details. Agents running on the owner's
workstation over the private network may see everything; agents reaching Cérebro from the internet must never
see restricted content, whatever token they hold.

## Decision

- Sensitivity levels: `public < internal < restricted`.
- The backend runs two HTTP listeners on different ports:
  - **private** (exposed only via Tailscale): ceiling `restricted`;
  - **public** (reachable only by the `cloudflared` container, disabled initially): ceiling `internal`.
    *Amended by ADR-0028:* the public ceiling is configurable (default `internal`); the owner's deployment uses
    `restricted`, so the token ceiling decides. The owner's browser there stays capped at `internal`.
- Each agent token has its own ceiling.
- `effective_ceiling = min(listener_ceiling, token_ceiling)`. The token can only lower the ceiling.
- The listener is identified by the socket the request arrived on, **never** by headers such as
  `X-Forwarded-For` or `CF-Connecting-IP`.
- Notes above the effective ceiling are invisible everywhere (search, hubs, neighbors, graph); reading one by id
  returns not found.
- P0 notes cannot be `restricted`.

## Consequences

- Two independent locks (network path + token) protect restricted content.
- Hub rendering and graph queries must filter by ceiling at read time.

## Alternatives considered

- Token-only ceilings: one leaked token would expose restricted notes over the internet.
- IP allowlists via headers: spoofable behind proxies.
