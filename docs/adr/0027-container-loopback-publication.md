# ADR-0027: Publish container ports only on host loopback

- **Status:** accepted
- **Date:** 2026-11-26
- **Amends:** the bind-address rules of [ADR-0017](0017-cli-and-mcp-transports.md) and [ADR-0019](0019-owner-browser-api.md).

## Context

Docker bridge port forwarding delivers traffic to a container interface. A service bound to loopback inside the
container cannot receive that traffic.

## Decision

Only the HTTP server accepts `--container`, which selects `0.0.0.0` inside the container's network namespace.
Without this flag the HTTP server accepts only numeric loopback addresses. The stdio bridge remains loopback-only.

Never publish a Cérebro listener outside host loopback. Use a dedicated Docker bridge network per environment and
publish each enabled port as `127.0.0.1:PORT:PORT`. Host networking is incompatible with this mode. Keep the public
listener disabled unless explicitly enabled. The accepting port determines the ceiling. Tailscale Serve terminates
HTTPS for the private listener as specified in [ADR-0010](0010-network-access.md).

## Consequences

Containers sharing the service's bridge can reach its listeners directly. Keep unrelated containers off this network.
The deployment checks the resolved port bindings before startup and verifies the running container's published ports.

## Alternatives considered

Host networking would give the process the host network namespace and remove project-level network isolation.
