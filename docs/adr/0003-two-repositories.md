# ADR-0003: Two repositories — public application, private content

- **Status:** accepted
- **Date:** 2026-11-24
- **Note (2026-11-26):** the owner confirmed that the application repository is currently private. The ban below on
  real notes, personal data and deployment details still applies, so the repository stays publishable.

## Context

The application is meant to be open source so it can evolve publicly and be reused by others. The knowledge
of a given instance is personal and partly sensitive.

## Decision

- `cerebro` (public): application code, docs, ADRs, deployment templates. Licensed Apache-2.0.
- Content repository (private, one per instance): notes and schema only. Also serves as the off-server
  backup of the knowledge, pushed to a private remote.
- The public repo must never contain real notes, personal data, hostnames, IPs, domains or tailnet names.
  All deployment-specific values come from configuration (`.env`, config file) with `.env.example`
  placeholders.
- `cerebro init-content` creates an empty content repo with the default `schema/`.

## Consequences

- Clean separation of code history and knowledge history.
- Anyone can run their own instance from the public repo.
- Documentation must use placeholders like `<cerebro-host>`.

## Alternatives considered

- Single private repo: simpler, but blocks open-sourcing and mixes histories.
