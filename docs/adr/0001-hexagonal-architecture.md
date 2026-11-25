# ADR-0001: Hexagonal architecture with a dependency-free core

- **Status:** accepted
- **Date:** 2026-11-24

## Context

Cérebro must stay solid over years while storage, transports (MCP, HTTP) and LLM providers evolve quickly.
The owner wants the inside of the application to depend as little as possible on external things, using
libraries only where they are needed.

## Decision

- Use ports and adapters. Layers: `domain` → `application` (use cases + ports) → `adapters` (inbound and
  outbound) → `bootstrap` (composition root).
- `cerebro.domain` uses only the Python standard library. `cerebro.application` depends only on the domain.
  Ports are `typing.Protocol`s.
- Third-party libraries live only in adapters and bootstrap.
- Enforce with `import-linter` in CI.
- Every port has an in-memory fake and a contract test suite shared by fake and real adapters.

## Consequences

- Core logic is fast to test and immune to library churn.
- Swapping storage, provider or transport means writing an adapter, not touching rules.
- More boilerplate (DTOs, mapping between frontmatter dicts and domain objects). Accepted.

## Alternatives considered

- Layered "FastAPI app with models everywhere": faster to start, but couples rules to the framework and to
  Pydantic.
- Clean Architecture with more layers: more ceremony than this project needs.
