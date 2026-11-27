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

## WSL acceptance

On 2026-11-26 the hml project ran with synthetic data on WSL2 (Linux amd64), Docker Engine 29.5.3 and
Compose 5.1.4. At source revision `f6757e1`, the startup guard rejected the ordinary bridge created by
this Engine, which reports IPv4 enabled and IPv6 disabled as explicit options. A regression reproduced
the failure. After correcting the runtime check, the image was built again from
`3cfc8d6276f60d13c6d576b48fc5d1abda2b0eb5` using Compose. The local image ID was
`sha256:09b2a0121e58a72b1ee102af1751e2ae6578a6102bf8f9161b501a3f17ec4bec`.

| Check | Observed |
|---|---|
| Startup | Initialized the content checkout, committed a synthetic P0 note, provisioned an owner password and issued a read token. The deployment start script completed and Docker reported `healthy`. |
| Storage and identity | Docker inspection showed the non-root operator UID/GID and four distinct content, state, index and secrets mounts. The state and index databases appeared in their respective mounts. |
| Exposure | The project bridge had one published TCP port at `127.0.0.1`, with matching host and container port numbers. WSL `ss` and the Windows TCP table each showed one loopback listener. Connection attempts to both assigned non-loopback WSL IPv4 addresses failed. |
| MCP | Unauthenticated overview returned HTTP 401. The original read token returned HTTP 200 with the seeded P0 note. |
| Browser | Chromium 159.0.8154.6 used a disposable local HTTPS proxy, signed in as the owner, opened the seeded note from Overview and displayed its body in a restricted session. |
| Restart | After a Compose restart the container had a new start time and returned to healthy. The Git HEAD, note, read token and owner login persisted. A fresh Chromium session read the same note. The temporary stack was then removed. |

At the time of this acceptance the server steps were still ahead. Update (2026-11-26): the owner confirmed that the
server deployment is done and in production; the restore drill on hml is the one checkpoint still pending
([roadmap](../roadmap.md#pending-owner-actions)).
