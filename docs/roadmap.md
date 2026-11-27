# Roadmap

Work one phase at a time. A phase is done only when all its acceptance criteria are met and this file and the
"Current status" section of `AGENTS.md` are updated. Plans and delivery records of finished phases (criteria
evidence, pull requests, decisions) are in [`plans/archive/`](plans/archive/), including the
[original scope and acceptance criteria](plans/archive/delivered-phases.md).

Status legend: ⬜ not started · 🟨 in progress · ✅ done

## Pending owner actions

Manual checkpoints:

| From | What the owner still does |
|---|---|
| Phase 3 | Run the [manual Claude Code and Codex checks](agent-integration.md#manual-check-in-claude-code-and-codex) and decide the [security checkpoint](plans/archive/phase-3.md#security-checkpoint-for-the-owner). |
| Phase 5 | Complete the [browser walkthrough](plans/archive/phase-5.md#7-open-the-interface-from-wsl) (translation, both listeners) and the visual and [security review](plans/archive/phase-5.md#security-checkpoint-for-the-owner). |
| Phase 7 | Run the [real-client write walkthrough](agent-integration.md#inspect-the-same-client-writes-in-recent-changes) with Claude Code and Codex. |

The Phase 4 checkpoint covered the Inbox, which [ADR-0023](adr/0023-no-legacy-compatibility.md) removed; it cannot
be run against current code. Its [record](plans/archive/phase-4.md#security-checkpoint-for-the-owner) is kept.

---

## ✅ Phase 0 — Bootstrap

`backend/` skeleton with `uv`, pytest, ruff, mypy `--strict`, import-linter contracts and GitHub Actions CI. The
"standard library only" contract is a custom type in `backend/devtools/import_contracts.py`, checked continuously by
`backend/tests/architecture/test_import_contracts.py`. *Done 2026-11-24.*

## ✅ Phase 1 — Domain and application core

[Domain model](domain-model.md) in pure Python, use cases against in-memory fakes and port contract tests.
*Done 2026-11-25*; the owner approved the security checkpoint the same day.
[Record](plans/archive/phase-1.md).

## ✅ Phase 2 — Outbound adapters

Filesystem note repository, git, Fernet, SQLite index and state store, system adapters and a minimal composition
root. *Done 2026-11-25.* Owner decisions: no MAC for token rows
([accepted risk](architecture.md#accepted-risks-owner-decision-2026-11-25)); `types-pyyaml` approved as a development
dependency. [Record](plans/archive/phase-2.md).

## ✅ Phase 3 — CLI and read-only MCP

Owner CLI and four authenticated MCP read tools over streamable HTTP and a stdio bridge.
*Delivered and approved 2026-11-25*; manual checks pending (above). [Record](plans/archive/phase-3.md).

## ✅ Phase 4 — Inbox

Retired. The proposal and approval flow, *delivered 2026-11-25*, was replaced by Phase 7 and removed by
[ADR-0023](adr/0023-no-legacy-compatibility.md). [Record](plans/archive/phase-4.md).

## ✅ Phase 5 — HTTP API and web UI

Owner browser login, overview, search, sanitized reader, graph, editor and health on the same listeners as MCP
([ADR-0019](adr/0019-owner-browser-api.md)). *Delivered and approved*; manual reviews pending (above).
[Record](plans/archive/phase-5.md).

## ✅ Phase 7 — Direct agent writes

Delivered before Phase 6. Agents with the `write` scope create and update notes after deterministic validation, with
attributed Git commits, optimistic concurrency and per-token limits; the write guide is loaded on demand and the
private **Recent changes** view shows actor, time, note and diff ([ADR-0020](adr/0020-direct-agent-writes.md)).
Follow-ups: readable ids ([ADR-0021](adr/0021-readable-note-ids.md)), the `core` scope
([ADR-0022](adr/0022-core-scope.md)), legacy removal (ADR-0023) and create replay from Git
([ADR-0024](adr/0024-create-replay-from-git.md)). An invalid committed note blocks edits and links to that note.
Other notes remain writable, and server errors release the write quota reservation. [Record](plans/archive/phase-7.md).

Pre-deploy maintenance preserves dirty content checkouts under the state directory before startup, writes and rebuilds.

## 🟨 Phase 6 — Deploy (hml, prod)

[Approved plan and local validation record](plans/phase-6.md). Server installation is a separate owner checkpoint.

- WSL acceptance passed with synthetic content, including startup, health, MCP, browser access, loopback publication and restart persistence.
- Dockerfile(s), `deploy/compose.yaml` with `cerebro-hml` and `cerebro-prod` projects, healthchecks,
  restart policy, log rotation, separate volumes.
- Finish on the server. Validate in hml, then promote the same image to prod (`cerebro-backend:git-<sha>`).
- `tailscale serve` HTTPS in front of the private listener (owner browser).
- Public listener behind a dedicated Cloudflare Tunnel: Access protects the web UI, `/mcp` bypasses Access and needs
  the bearer token. The public ceiling is `restricted`, so each token's ceiling decides
  ([ADR-0028](adr/0028-token-decides-public-access.md)). Agents need only the public URL and a token.
- Backup pushes the content repository to a private GitHub remote, snapshots SQLite and copies the owner verifier. Escrow the Fernet key separately.
- Scheduled jobs never run between 00:00 and 01:00 local time (server shuts down daily at 01:00).

**Audit items addressed in deployment preparation**

- **12. Docker bind and exposure.** Container mode uses the private listener on a project bridge. Compose checks and the WSL trial verified a single host-loopback publication. [ADR-0027](adr/0027-container-loopback-publication.md)
- **13. Backup.** The backup command pairs the content revision with a SQLite snapshot and the owner verifier. Quarantine and the derived index are excluded. The maintenance example schedules Git garbage collection separately. [Backup and restore](../deploy/backup.md)
- **14. Operating documentation.** The README covers credentials, tokens and startup. The architecture inventory places the owner verifier under the state directory and lists the implemented adapters. [Quickstart](../README.md) · [Architecture](architecture.md)

**Pending server and workstation acceptance:** hml and prod running on the server; Claude Code and Codex on the workstation use prod via
the public URL and their tokens; restore of content + state tested on hml.

## ⬜ Phase 8 — Later

- **Next:** OAuth on `/mcp` for cloud chat connectors (ChatGPT, claude.ai) that cannot send a static bearer header.
- "Agent path" view in the graph (which notes an agent read in a session).
- Semantic search (embeddings) as an additional `SearchIndex` adapter, if FTS proves insufficient.
