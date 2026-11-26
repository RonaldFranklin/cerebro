# Phase 6: deployment preparation

The owner authorized seven deliveries, one pull request each. Work and validation in this repository use WSL and
synthetic data. The server agent executes the documented installation and checks hml before promoting to prod.

| Delivery | Result |
|---|---|
| 1. Container image and bind policy | Multi-stage web/backend image, non-root runtime, HTTP healthcheck and [ADR-0027](../adr/0027-container-loopback-publication.md). |
| 2. Compose | Separate hml/prod projects and storage, host-loopback publication test (including rejection of host networking with no published ports), local build and rollback tags. |
| 3. Backup | Private content push and consistent SQLite backup, excluding quarantine and the index. |
| 4. Server runbook | Executable deployment, validation, update, rollback and removal steps. |
| 5. Workstation clients | User-level Claude Code and Codex setup in WSL via the tailnet. |
| 6. Operator documentation | Short quickstart and accurate runtime/storage paths. |
| 7. WSL acceptance | Local Compose startup, health, MCP, browser, port exposure and restart persistence evidence. |

## Image pin evidence

The Dockerfile uses the official multi-platform Debian Bookworm images for Node 26.13.1 and Python 3.14.10. The
Docker Official Images registry inventory had recorded both pinned digests by 2026-11-18 23:54 UTC. Their age at
selection on 2026-11-26 exceeded seven days. The Python image includes the Git CLI. Build tools pnpm 12.22.0 and
uv 0.13.9 were published on 2026-11-17. The Dockerfile verifies the package checksums and uses the application
lockfiles. Supported build platforms are Linux amd64 and arm64.
[Node image inventory](https://github.com/docker-library/repo-info/blob/628b21c3b985ec9790c97d7cc42526d763d22c15/repos/node/remote/26.13.1-bookworm.md) ·
[Python image inventory](https://github.com/docker-library/repo-info/blob/628b21c3b985ec9790c97d7cc42526d763d22c15/repos/python/remote/3.14.10-bookworm.md) ·
[pnpm amd64 metadata](https://registry.npmjs.org/@pnpm/exe.linux-x64) · [pnpm arm64 metadata](https://registry.npmjs.org/@pnpm/exe.linux-arm64) ·
[uv wheel metadata](https://pypi.org/pypi/uv/0.13.9/json).
