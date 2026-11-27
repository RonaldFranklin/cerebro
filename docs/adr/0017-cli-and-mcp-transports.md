# ADR-0017: Owner CLI and read-only MCP transports

- **Status:** accepted, amended by [ADR-0020](0020-direct-agent-writes.md) (`cerebro_write` and `cerebro_write_guide` replace `cerebro_propose`); container binding amended by [ADR-0027](0027-container-loopback-publication.md); public ceiling configurable per [ADR-0028](0028-token-decides-public-access.md)
- **Date:** 2026-11-25

## Context

Phase 3 exposes the existing use cases to the local owner and to agents. ADR-0005 ties a request's
ceiling to the accepting socket and its token. ADR-0010 places HTTPS termination at Tailscale Serve.
ADR-0014 defines the agent tool schemas.

## Decision

- In Phase 3 register the four read tools defined in ADR-0014. Its fifth tool, cerebro_propose, arrives in Phase 4.
- Use standard-library argparse for the local owner CLI and tomllib for settings.
- Use the official Python mcp SDK and Uvicorn for stateless Streamable HTTP. PR 7 records the resolved
  versions and upload dates for both packages and their transitive dependencies under ADR-0013's seven-day rule.
- Bind both listeners to 127.0.0.1 by default, on distinct ports. The private socket has a fixed restricted
  ceiling and the public socket a fixed internal ceiling. Enable only the private listener by default.
  At startup reject non-loopback binds for either Phase 3 listener. The accepting port supplies the ceiling even when a proxy sends forwarding
  headers. Tailscale Serve provides private network exposure over HTTPS.
- Accept tokens exclusively in the Authorization header using the Bearer scheme. Reject token query parameters.
  Exclude tokens, note bodies and proposal payloads from application, server, access and error logs.
- For local development, the stdio adapter bridges bounded MCP messages to the configured private loopback
  endpoint. It obtains the credential from its process environment and sends it only in the HTTP bearer
  header. Disable redirects and proxy discovery. Connect directly to a numeric loopback address. Both transports reach the same authentication and authorization checks.
- Use an SDK client against a dev instance with sample content, a restricted note and an internal token
  for automated acceptance. Provide exact commands for the owner to test Claude Code and Codex manually in
  docs/agent-integration.md. Keep all test configuration scoped to the test workspace or process. Agent runs
  must preserve user-level client configuration, including ~/.claude and ~/.codex.

## Consequences

Each request authenticates against the current token record. Stateless HTTP has no session replay.
The dev stdio bridge requires a running private listener. The owner reviews the security checkpoint after delivery.

## Alternatives considered

Typer adds a dependency for a small command tree. A native stdio authentication extension would add a
second credential protocol. A wildcard listener default would expose the backend beyond the intended local proxy.
