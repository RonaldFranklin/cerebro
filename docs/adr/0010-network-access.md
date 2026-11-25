# ADR-0010: Network access — Tailscale first, Cloudflare Tunnel later

- **Status:** accepted
- **Date:** 2026-11-24

## Context

Cérebro runs on a home server. Agents on the owner's workstation must reach it securely, inside and outside
the home network. In the future, cloud-hosted agents may need access. The reference deployment already uses
Tailscale and Cloudflare Tunnels for other services.

## Decision

- **Now:** the private listener is exposed only through `tailscale serve` with HTTPS (automatic certificate,
  tailnet-only). The workstation joins the tailnet. No router port forwarding.
- **Later (Phase 8):** a dedicated Cloudflare Tunnel for Cérebro (not shared with other projects) reaches only
  the public listener, protected by Cloudflare Access (login + MFA for the UI, service tokens for agents).
  Ceiling `internal`.
- Tokens never travel over plain HTTP outside the host.
- All hostnames/domains are configuration.

## Consequences

- Restricted content is reachable only through the tailnet.
- Within the home LAN, Tailscale connects directly, so there is no meaningful performance cost.
- Cloud agents (e.g. claude.ai connectors) are out of scope until the public listener exists.

## Alternatives considered

- Plain HTTP on the LAN: tokens in clear text; no access outside home.
- Cloudflare-only: would expose the service publicly from day one.
