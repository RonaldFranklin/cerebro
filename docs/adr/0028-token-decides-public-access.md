# ADR-0028: Agents use the public entrance and their token sets the ceiling

- **Status:** accepted
- **Date:** 2026-11-26
- **Amends:** [ADR-0005](0005-sensitivity-and-access-ceiling.md) (fixed public ceiling) and
  [ADR-0010](0010-network-access.md) (Tailscale for agents, Cloudflare later with service tokens); also the fixed
  public ceiling in [ADR-0017](0017-cli-and-mcp-transports.md).

## Context

The owner wants any authorized AI agent to use Cérebro with only a public URL and a bearer token. Joining a
tailnet or holding a Cloudflare service token is a burden for each client and is impossible for some. With the
public listener fixed at `internal`, an agent that needs restricted notes could not use the public entrance.

## Decision

- The public listener's ceiling is configured with `cerebro-mcp --public-ceiling public|internal|restricted`.
  The default stays `internal`; `restricted` requires an explicit setting. The private listener stays
  `restricted`. The accepting socket still sets the listener ceiling and
  `effective_ceiling = min(listener_ceiling, token_ceiling)` still holds. Headers never select a listener.
- The owner's deployment sets the public ceiling to `restricted`, so the token ceiling alone decides what each agent
  sees. The owner issues each token with the lowest ceiling that agent needs.
- The owner's browser on the public listener stays capped at `internal`, whatever the public MCP ceiling. Owner
  edits, change history and health remain on the private listener only.
- A dedicated Cloudflare Tunnel publishes the public listener. Cloudflare Access protects the web UI. A bypass
  application on exactly the `/mcp` path lets agents reach MCP with their Cérebro bearer token alone. Tailscale
  Serve remains the owner's private entrance.

## Consequences

- **Accepted risk:** a leaked token with a `restricted` ceiling gives access to restricted notes from the internet
  until the owner revokes it. A leaked `write` token can change notes from the internet within its scopes and
  write quota. Revocation takes effect on the next request.
- Restricted notes stay encrypted at rest. The server decrypts them for an authorized token, exactly as on the
  private listener.
- MCP on the public listener has no Cloudflare identity layer. Cérebro authenticates the bearer token before it
  parses the request body, so anonymous callers only receive 401. What remains is the 64 KiB body limit, the
  per-token write quota, query and Origin rejection, the no-logging rules and a server-wide concurrency bound
  (`MAX_CONCURRENCY`, beyond which requests get 503 on both listeners). There is no per-client request rate limit in
  Cérebro; the deployment adds a Cloudflare rate-limiting rule on `/mcp`.
- Client token files are readable by any command running as that user, and now work from the internet. Use them
  only in trusted projects and revoke a token as soon as it may be exposed.
- Cloud chat connectors that cannot send a static header still need OAuth on `/mcp` (roadmap).

## Alternatives considered

- Keep `internal` on the public listener: agents without the tailnet would never see restricted notes.
- Cloudflare Access service tokens for agents: every client would need two extra headers, and most MCP clients
  cannot send them.
- Raise the public ceiling for the browser too: it would widen the owner UI beyond the decision, which concerns agents.
