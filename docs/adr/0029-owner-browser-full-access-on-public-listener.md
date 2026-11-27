# ADR-0029: The signed-in owner has full access on both browser listeners

- **Status:** accepted
- **Date:** 2026-11-27
- **Amends:** [ADR-0019](0019-owner-browser-api.md) (writes and health require the private listener),
  [ADR-0020](0020-direct-agent-writes.md) (change history for the private owner only) and
  [ADR-0028](0028-token-decides-public-access.md) (owner browser capped at `internal` on the public listener).

## Context

The owner uses the web UI only through the public URL. There the browser session was capped at `internal`, and
editing, Recent changes and health existed only on the private listener, so the owner could not use them. In the
documented deployment the public web UI is behind Cloudflare Access (owner identity with MFA) and then the owner
password. ADR-0028 already lets an agent with a `restricted` token read restricted notes through that entrance.

## Decision

- A signed-in owner browser session has the ceiling `restricted` on both listeners. It can edit notes, read change
  history and read the health report on both.
- `--public-ceiling` sets the MCP ceiling of the public listener only. It never changes the owner's browser.
- Nothing else changes: MCP authentication and `min(listener_ceiling, token_ceiling)` for tokens, the private
  listener, and the browser protections of ADR-0019 (password, shared login throttle, listener-bound sessions and
  cookies, exact Origin, CSRF token, CSP). The listener is still the accepting socket, never a header.
- The browser API still refuses edits and history, and the health use case still refuses the report, for any
  session below `restricted`.
- **Deployment requirement:** a public listener reachable from a network must sit behind an identity layer that
  admits only the owner, with MFA, on every path except exactly `/mcp`.

## Consequences

- **Accepted risk:** from the internet, whoever passes the identity layer and knows the owner password can read
  restricted notes, edit any note and read history and health.
- **Cérebro does not verify the identity layer.** It checks no Cloudflare Access assertion. If Access is missing,
  or a bypass covers more than `/mcp`, the owner password and the login throttle are all that protect restricted
  notes and edits. This applies to any instance started with `--enable-public`, including one with the default
  `internal` MCP ceiling. Verify the Access redirect before the first sign-in and after every change to it.
- The login throttle is shared by both listeners. Without the identity layer, anyone who reaches the public login
  can exhaust it and lock the owner out of both.
- A TLS-terminating proxy such as Cloudflare can see restricted notes, edits, change diffs and the session cookie
  of the public listener. It already saw the password, and the restricted notes that agents read over MCP.
- Both browser cookies now carry equal authority. Browsers send host-only cookies to every port of a host, so the
  listeners keep distinct browser hostnames whenever their proxies are separate trust boundaries.

## Alternatives considered

- A setting for the public browser ceiling, defaulting to `internal`: safer for an instance without an identity
  layer, but one more setting for a single-owner service whose owner decided on full access.
- Verify the Cloudflare Access assertion in the application: it would enforce the requirement, at the cost of a
  provider-specific verifier, key retrieval and a signature dependency. It can be added by a later ADR.
- Use only the private listener: the owner does not use the tailnet for the web UI.
