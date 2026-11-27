# Architecture Decision Records

| ADR | Title | Status |
|---|---|---|
| [0001](0001-hexagonal-architecture.md) | Hexagonal architecture with a dependency-free core | accepted |
| [0002](0002-markdown-git-source-of-truth.md) | Markdown + git as source of truth, SQLite as derived index | accepted |
| [0003](0003-two-repositories.md) | Two repositories — public application, private content | accepted |
| [0004](0004-priority-levels-and-context-budget.md) | Priority levels and context budgets | accepted, amended by [0020](0020-direct-agent-writes.md) and [0025](0025-core-word-budget.md) |
| [0005](0005-sensitivity-and-access-ceiling.md) | Sensitivity levels and access ceiling by listener and token | accepted, amended by [0028](0028-token-decides-public-access.md) and [0029](0029-owner-browser-full-access-on-public-listener.md) |
| [0006](0006-write-path-inbox-curator.md) | Write path — Inbox, validation, curator, approval | superseded by [0020](0020-direct-agent-writes.md) |
| [0007](0007-curator-provider-agnostic.md) | Curator is provider-agnostic and optional | superseded by [0020](0020-direct-agent-writes.md) |
| [0008](0008-encryption-of-restricted-notes.md) | Encrypt restricted notes at rest with Fernet | accepted |
| [0009](0009-english-only.md) | English only, browser-translatable UI | accepted, amended by [0020](0020-direct-agent-writes.md) |
| [0010](0010-network-access.md) | Network access — Tailscale first, Cloudflare Tunnel later | accepted, amended by [0028](0028-token-decides-public-access.md) |
| [0011](0011-single-user-instance.md) | Single-user instances | accepted |
| [0012](0012-environments-and-runtime.md) | Environments and server-only runtime | accepted, amended by [0020](0020-direct-agent-writes.md) |
| [0013](0013-stack-and-dependency-policy.md) | Stack and dependency policy | accepted |
| [0014](0014-mcp-tool-surface.md) | Small MCP tool surface | accepted, amended by [0020](0020-direct-agent-writes.md) |
| [0015](0015-hub-membership-computed-at-read-time.md) | Hub membership is computed at read time | accepted |
| [0016](0016-curator-never-sees-restricted.md) | The curator never receives restricted notes | superseded by [0020](0020-direct-agent-writes.md) |
| [0017](0017-cli-and-mcp-transports.md) | Owner CLI and read-only MCP transports | accepted, amended by [0020](0020-direct-agent-writes.md), [0027](0027-container-loopback-publication.md) and [0028](0028-token-decides-public-access.md) |
| [0018](0018-inbox-admission.md) | Validate Inbox submissions before durable admission | superseded by [0020](0020-direct-agent-writes.md) |
| [0019](0019-owner-browser-api.md) | Same-origin owner browser API and local web toolchain | accepted, amended by [0020](0020-direct-agent-writes.md), [0027](0027-container-loopback-publication.md) and [0029](0029-owner-browser-full-access-on-public-listener.md) |
| [0020](0020-direct-agent-writes.md) | Validated direct agent writes and owner change history | accepted, amended by [0021](0021-readable-note-ids.md) and [0022](0022-core-scope.md); compatibility requirements superseded by [0023](0023-no-legacy-compatibility.md); replay binding amended by [0024](0024-create-replay-from-git.md), write admission amended by [0026](0026-invalid-committed-notes.md), history access amended by [0029](0029-owner-browser-full-access-on-public-listener.md) |
| [0021](0021-readable-note-ids.md) | Readable agent-created note IDs and default write quota | accepted |
| [0022](0022-core-scope.md) | A `core` scope guards the global rules | accepted, amended by [0025](0025-core-word-budget.md) |
| [0023](0023-no-legacy-compatibility.md) | Drop compatibility with the retired Inbox | accepted |
| [0024](0024-create-replay-from-git.md) | Detect create replays from the committed note | accepted |
| [0025](0025-core-word-budget.md) | Reduce the combined core budget | accepted |
| [0026](0026-invalid-committed-notes.md) | Isolate invalid committed notes on writes | accepted |
| [0027](0027-container-loopback-publication.md) | Publish container ports only on host loopback | accepted |
| [0028](0028-token-decides-public-access.md) | Agents use the public entrance and their token sets the ceiling | accepted, amended by [0029](0029-owner-browser-full-access-on-public-listener.md) |
| [0029](0029-owner-browser-full-access-on-public-listener.md) | The owner can opt in to full browser access on the public listener | accepted |
| [0031](0031-usage-counters-and-live-activity.md) | Token usage counters and the owner's live activity channel | proposed |

New decisions: copy [`0000-template.md`](0000-template.md), use the next number, add it to this table.
