# ADR-0020: External curator through scoped MCP tools

- **Status:** proposed for owner approval with the Phase 7 plan
- **Date:** 2026-11-26
- **On acceptance:** supersedes [ADR-0007](0007-curator-provider-agnostic.md) and extends the five-tool surface of [ADR-0014](0014-mcp-tool-surface.md) for curator credentials only. [ADR-0016](0016-curator-never-sees-restricted.md) continues to apply.

## Context

The owner runs the curator in a separately authenticated agent such as Claude Code or Codex. A future cloud MCP client can use the same protocol, subject to deployment authorization. Material returned to that client can reach its model provider. The server must control disclosure before returning MCP results.

## Decision

Keep NullCurator as the only internal curator adapter and preserve atomic admission under ADR-0018. A reviewable submission can also be eligible for an external recommendation. The external client selects its subscription and model. Cérebro makes no inference calls and holds no model-provider credentials, token prices or spending ledger.

Introduce a dedicated agent-token scope, `curate`, with a maximum ceiling of `internal`. Issue it separately from `read` and `propose`. Enforce that invariant during issuance and use, across the entire token, and filter both discovery and invocation on each authenticated request. The sole capabilities of this credential are fetching the packaged guide, listing eligible proposals, reading a bounded context, and submitting a structured recommendation. The listener, token, original admission ceiling and ADR-0016 bound disclosure. Restricted submissions go directly to owner review and stay invisible through every curator tool.

Reuse the application policy from CurateProposal for the external use cases. Treat all proposal and note text as untrusted. The server validates a recommendation, its sensitivity floor, current eligibility, context revision and target preconditions. It binds attribution to the authenticated curator and preserves the original proposal. A recommendation, including rejection, awaits an independent owner decision. Only existing owner review and apply operations can approve or commit.

## Consequences

The same guide and tool contract work with multiple clients. The owner can always review directly if no curator is running. The server can enforce its disclosure and write boundaries but cannot erase content already delivered to a client or attest to its model, billing or off-platform activity. The Phase 7 acceptance uses synthetic data and the installed MCP SDK. Real-client use stays under owner control.

## Alternatives considered

The earlier internal worker and paid provider adapters require service-held credentials and per-provider operations. Granting the existing owner Inbox interface to an agent would expose restricted proposals and owner actions. A dedicated MCP scope provides the four capabilities required for curation.
