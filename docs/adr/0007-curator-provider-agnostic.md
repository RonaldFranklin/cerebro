# ADR-0007: Curator is provider-agnostic and optional

- **Status:** accepted
- **Date:** 2026-11-24

## Context

The owner will use a general-purpose model from Anthropic (Claude) or OpenAI (GPT) for the curator and will
decide which later.

## Decision

- The curator is behind the `Curator` port. Adapters: Anthropic, OpenAI, and `NullCurator`.
- `NullCurator` returns `needs_owner` for every proposal, so the whole write path works without any LLM
  (Phases 4–6).
- Provider, model names and API keys come from configuration/secrets.
- Two-tier usage: a small model for triage (reject obvious noise and duplicates), a larger model for
  merge/create decisions.
- The curator writes notes in English regardless of the proposal's language.
- Token usage and cost per curation are logged.

## Consequences

- No vendor lock-in; the provider decision is deferred without blocking development.
- Curator prompts and output schema must be provider-neutral and validated by the domain.

## Alternatives considered

- Local model on the server: free, but the server has no GPU; quality and latency would suffer.
