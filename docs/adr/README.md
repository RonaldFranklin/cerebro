# Architecture Decision Records

| ADR | Title | Status |
|---|---|---|
| [0001](0001-hexagonal-architecture.md) | Hexagonal architecture with a dependency-free core | accepted |
| [0002](0002-markdown-git-source-of-truth.md) | Markdown + git as source of truth, SQLite as derived index | accepted |
| [0003](0003-two-repositories.md) | Two repositories — public application, private content | accepted |
| [0004](0004-priority-levels-and-context-budget.md) | Priority levels and context budgets | accepted |
| [0005](0005-sensitivity-and-access-ceiling.md) | Sensitivity levels and access ceiling by listener and token | accepted |
| [0006](0006-write-path-inbox-curator.md) | Write path — Inbox, validation, curator, approval | accepted |
| [0007](0007-curator-provider-agnostic.md) | Curator is provider-agnostic and optional | accepted |
| [0008](0008-encryption-of-restricted-notes.md) | Encrypt restricted notes at rest with Fernet | accepted |
| [0009](0009-english-only.md) | English only, browser-translatable UI | accepted |
| [0010](0010-network-access.md) | Network access — Tailscale first, Cloudflare Tunnel later | accepted |
| [0011](0011-single-user-instance.md) | Single-user instances | accepted |
| [0012](0012-environments-and-runtime.md) | Environments and server-only runtime | accepted |
| [0013](0013-stack-and-dependency-policy.md) | Stack and dependency policy | accepted |
| [0014](0014-mcp-tool-surface.md) | Small MCP tool surface | accepted |
| [0015](0015-hub-membership-computed-at-read-time.md) | Hub membership is computed at read time | accepted |
| [0016](0016-curator-never-sees-restricted.md) | The curator never receives restricted notes | accepted |

New decisions: copy [`0000-template.md`](0000-template.md), use the next number, add it to this table.
