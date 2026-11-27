# ADR-0029: The owner can opt in to full browser access on the public listener

- **Status:** accepted
- **Date:** 2026-11-27
- **Amends:** [ADR-0005](0005-sensitivity-and-access-ceiling.md) (owner browser capped on the public listener),
  [ADR-0019](0019-owner-browser-api.md) (writes and health require the private listener),
  [ADR-0020](0020-direct-agent-writes.md) (change history for the private owner only) and
  [ADR-0028](0028-token-decides-public-access.md) (owner browser capped at `internal` on the public listener).

## Context

The owner uses the web UI only through the public URL. There the browser session was capped at `internal`, and
editing, Recent changes and health existed only on the private listener, so the owner could not use them. In the
documented deployment the public web UI is behind Cloudflare Access (owner identity with MFA) and then the owner
password. ADR-0028 already lets an agent with a `restricted` token read restricted notes through that entrance.

The repository is public and not every instance has such a layer: the Docker quickstart starts the public listener
with nothing but the owner password in front of it. The default must be safe for those instances.

## Decision

- `cerebro-mcp --public-browser-ceiling internal|restricted` sets the ceiling of the signed-in owner's browser on the
  public listener (Compose: `PUBLIC_BROWSER_CEILING`). The default is `internal`.
- With `restricted`, that session has the ceiling `restricted` and can edit notes, read change history and read the
  health report, whatever `--public-ceiling` is.
- With `internal`, the previous behaviour holds: the session's ceiling is the lower of `--public-ceiling` and
  `internal`, and edits, history and health are refused there.
- Writes, history and health require a `restricted` owner session, on whichever listener it was issued. The private
  listener's session is always `restricted`.
- `--public-ceiling` keeps setting the MCP ceiling of the public listener.
- Nothing else changes: MCP authentication and `min(listener_ceiling, token_ceiling)` for tokens, the private
  listener, and the browser protections of ADR-0019 (password, shared login throttle, listener-bound sessions and
  cookies, exact Origin, CSRF token, CSP). The listener is still the accepting socket, never a header.
- **Deployment requirement for `restricted`:** the public listener must sit behind an identity layer that admits only
  the owner, with MFA, on every path except exactly `/mcp`.

## Consequences

- An instance that sets nothing behaves as before.
- **Accepted risk with `restricted`:** from the internet, whoever passes the identity layer and knows the owner
  password can read restricted notes, edit any note and read history and health.
- **Cérebro does not verify the identity layer.** It checks no Cloudflare Access assertion and starts with
  `restricted` even if there is none. If Access is missing, or a bypass covers more than `/mcp`, the owner password
  and the login throttle are all that protect restricted notes and edits. Verify the Access redirect before enabling
  the setting and after every change to Access or the tunnel.
- The login throttle is shared by both listeners. Without an identity layer, anyone who reaches the public login can
  exhaust it and lock the owner out of both, with either value.
- With `restricted`, a TLS-terminating proxy such as Cloudflare can see restricted notes, edits, change diffs and the
  session cookie of the public listener. It already saw the password, and the restricted notes that agents read over
  MCP. Both browser cookies then carry equal authority. Browsers send host-only cookies to every port of a host, so
  the listeners keep distinct browser hostnames whenever their proxies are separate trust boundaries.

## Alternatives considered

- Always `restricted`, with no setting: one setting less, but an instance without an identity layer would expose
  restricted notes and edits behind the password alone.
- Verify the Cloudflare Access assertion in the application: it would enforce the requirement, at the cost of a
  provider-specific verifier, key retrieval and a signature dependency. Left to a later ADR.
- Use only the private listener: the owner does not use the tailnet for the web UI.
