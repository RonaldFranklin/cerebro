# ADR-0025: Reduce the combined core budget

- **Status:** accepted
- **Date:** 2026-11-26
- **Amends:** the combined P0 word limit in [ADR-0004](0004-priority-levels-and-context-budget.md) and [ADR-0022](0022-core-scope.md).

## Context

Every session loads the current P0 notes through the overview. At the earlier limit of 1,500 words, a representative overview, search and read cost about 3,700 tokens.

## Decision

Limit all current P0 notes together to 800 words.

## Consequences

The MCP response-size test exercises the full core budget against the existing three-call target of at most 3,000 estimated tokens.

## Alternatives considered

Raising the lookup target would increase the context cost of every session.
