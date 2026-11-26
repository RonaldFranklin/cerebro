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
([ADR-0024](adr/0024-create-replay-from-git.md)). [Record](plans/archive/phase-7.md).

## ⬜ Phase 6 — Deploy (hml, prod)

- First validate the Docker image and Compose configuration in WSL dev with synthetic content. Check
  startup, health and persistence across a container restart before deploying to the server.
- Dockerfile(s), `deploy/compose.yaml` with `cerebro-hml` and `cerebro-prod` projects, healthchecks,
  restart policy, log rotation, separate volumes.
- Finish on the server. Validate in hml, then promote the same image to prod (`cerebro-backend:git-<sha>`).
- `tailscale serve` HTTPS in front of the private listener.
- Backups: content repo pushed to the private remote; state dir file backup; Fernet key escrow documented.
- Scheduled jobs never run between 00:00 and 01:00 local time (server shuts down daily at 01:00).

**Carried over from the 2026-11-26 audit (open, resolve in this phase):**

- **Docker bind and exposure.** The listeners accept only a loopback `--host`, and the accepting local port identifies
  the listener and so its ceiling, so how a container reaches them must be designed. The deployment must publish each port as `127.0.0.1:P:P` (same number on both
  sides) and include a test of what is exposed.
- **Backup.** `VersionControl.push` has no caller, so nothing pushes the content repository. Cérebro's git commands
  disable automatic garbage collection (`gc.auto=0`) and nothing else runs `git gc`.
- **Operating documentation.** The README quickstart stops after key provisioning (no owner password, token issue or
  server start). `architecture.md` places the owner password verifier in the secrets dir, but it is
  `<state_dir>/owner.verifier`; it also names a Scheduler adapter and package paths that do not exist.

**Acceptance:** hml and prod running on the server; Claude Code and Codex on the workstation use prod via
Tailscale; restore of content + state tested on hml.

## ⬜ Phase 8 — Later

- Public listener behind Cloudflare Tunnel + Cloudflare Access (service tokens), ceiling `internal`.
- "Agent path" view in the graph (which notes an agent read in a session).
- Semantic search (embeddings) as an additional `SearchIndex` adapter, if FTS proves insufficient.
