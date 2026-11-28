# AGENTS.md — Cérebro (application repository)

Instructions for coding agents (Claude Code, Codex, and others) working on **this repository**.
Claude Code reads this file automatically when no `CLAUDE.md` exists; keep it that way so one file serves every agent.

> This repo holds the **application** code. The notes live in a separate **content repository**. This repo is meant
> to be public: never add real notes, personal data or infrastructure details here.

## 1. What Cérebro is

A self-hosted, single-user knowledge base that gives every AI agent (Claude Code, Codex, ChatGPT, …) the same
curated, prioritized context through MCP, plus a web UI where the owner browses and edits notes, sees the knowledge
graph and inspects recent changes. Agents read within their sensitivity ceiling. Authorized agents create and
update notes immediately after validation, attributed in Git.

## 2. Read when you touch it

| Before you change or need | Read |
|---|---|
| goals, non-goals, success criteria | `docs/vision.md` |
| layers, ports, use cases, runtime, storage, dependencies | `docs/architecture.md` |
| note schema, priorities, sensitivity, secrets, agent writes, tokens | `docs/domain-model.md` |
| phase scope, acceptance criteria, owner checkpoints | `docs/roadmap.md` |
| any decided matter (check the index first) | `docs/adr/` |
| MCP clients and manual client checks | `docs/agent-integration.md` |
| history of finished phases | `docs/plans/archive/` |

Do not contradict an ADR; propose a new ADR that supersedes it instead.

## 3. Current status

Phases 0-7 are delivered. Phase 6 (deploy) is done and the server deployment is in production, except for one open
checkpoint: the restore drill on hml. Phase 8 (public owner UI and visual redesign) has every step of
[its plan](docs/plans/phase-8.md) merged; its deployment is pending.
See [the roadmap](docs/roadmap.md) for pending checkpoints. Update this section and the roadmap whenever a phase starts or finishes.

## 4. Non-negotiable rules

**Architecture**
- Dependencies point inward: `adapters → application → domain`. Never the other way around.
- `cerebro.domain` uses **only the Python standard library**. No PyYAML, no Pydantic, no FastAPI, nothing.
- `cerebro.application` depends only on `cerebro.domain` and defines ports as `typing.Protocol`.
- `cerebro.adapters.inbound` never imports `cerebro.application.ports` nor `cerebro.adapters.outbound`: inbound
  adapters talk only to use cases and DTOs, and wiring goes through `cerebro.bootstrap`.
- Third-party libraries are imported **only** inside `cerebro.adapters.*` and `cerebro.bootstrap`.
- Inbound adapters never import `cerebro.bootstrap`: the entry point wires use cases into them.
- These rules are enforced by `import-linter` in CI. A failing contract is a failing build.

**Write path**
- Inbound adapters call application use cases for every note change. Agent writes require explicit
  `write` authority and deterministic validation. Owner edits use `OwnerEditNote`.
- Every applied change creates a Git commit whose `Written-by` trailer names its authenticated agent, or
  `owner` for owner edits ([ADR-0020](docs/adr/0020-direct-agent-writes.md),
  [ADR-0023](docs/adr/0023-no-legacy-compatibility.md)). No code reads data from earlier releases.

**Security**
- Never commit secrets, tokens, keys, `.env` files or real personal data. Use `.env.example` with placeholders.
- No hostnames, IPs, domains or tailnet names of any real deployment in this repo. Everything is configuration.
- The effective sensitivity ceiling of a request is `min(listener_ceiling, token_ceiling)`. Never derive the
  listener from request headers. The public listener's ceiling is configuration (default `internal`,
  [ADR-0028](docs/adr/0028-token-decides-public-access.md)). It applies to tokens; the owner's browser there stays at most
  `internal` unless the owner opts in to `restricted`
  ([ADR-0029](docs/adr/0029-owner-browser-full-access-on-public-listener.md)).

**Language**
- Code, identifiers, comments, docs, commit messages and UI text: **English**.
- In conversation, answer in the language the person writes in, but keep every artifact in English.

## 5. How to work

- Small, verifiable steps. One phase at a time (**work only on the current phase**); within a phase, one use case or
  adapter at a time.
- **Git workflow:** one branch per step (e.g. `phase-6/docker-image`, `docs/<topic>`), never commit to `main`
  directly. Commits follow Conventional Commits (`feat:`, `fix:`, `docs:`, `build:`, `ci:`, `chore:`, `test:`,
  `refactor:`), one commit per step plus review fixes. Stage explicit paths, never `git add -A`. Never force-push:
  to fix a pull request, add commits (merge `main` into the branch if it moved). For every pull request:
  1. open it with `gh pr create`; the body ends with a "Decisions for review" section;
  2. **review before merging**, with fresh eyes that see only the diff and the relevant docs/ADRs:
     - agent runtimes **with sub-agents** (e.g. Claude Code): a fresh sub-agent. Use a stronger model for pull
       requests that touch security (access ceiling, tokens, encryption, the secret scanner, subprocess or
       filesystem hardening);
     - agent runtimes **without sub-agents** (e.g. Codex): a **new session** (empty context) that reads only
       `gh pr diff <n>`, `AGENTS.md`, the docs and ADRs the diff touches, and reports ranked findings with file, line
       and a failing scenario. The author does not review its own diff in the same session;
  3. fix what the review reports, push new commits, wait for CI again; review again when the fix is large or touches
     security;
  4. merge **only** with `scripts/merge-pr.sh <n>`. It waits for `gh pr checks <n> --watch --fail-fast`, confirms
     that GitHub's PR head is the pushed tip, and runs `gh pr merge --squash --delete-branch` only if every check is
     green. Do not call `gh pr merge` directly.
- **Do not merge — stop and hand over to the owner** when the pull request:
  - creates or changes an ADR, or changes rules in `docs/architecture.md` or `docs/domain-model.md`;
  - touches security: access ceiling, tokens, encryption, the secret scanner;
  - adds a new dependency (runtime or dev);
  - has a review finding that was not resolved.
  In that case, leave the pull request open with CI green and report what the owner should review.
- **Never merge while CI is pending or failing.** `scripts/merge-pr.sh` enforces it. If GitHub's GraphQL API is
  flaky, the script retries; do not bypass it.
- Do not start a step that depends on an unmerged pull request; branch from an up-to-date `main`.
- Domain and application code is written test-first with in-memory fakes. Adapters get integration tests and
  shared **contract tests** that both the fake and the real adapter must pass.
- Before declaring a task done: tests pass, `ruff` clean, `mypy --strict` clean, `import-linter` clean,
  docs/roadmap updated if behaviour or status changed.
- Significant decisions (new dependency, new port, change of a rule) need an ADR in `docs/adr/`
  (copy `0000-template.md`).
- Pin dependencies exactly (lockfiles committed). Prefer the standard library; add a dependency only when it
  removes real complexity, and record why in an ADR or in the dependency table in `docs/architecture.md`.
- Differentiate in reports: **planned**, **implemented**, **tested**.

### Working mode with the owner

- The owner approves **plans** (pull requests, design decisions) before a phase starts, and reviews a security
  checkpoint when a phase touches security. Inside an approved plan the agent works autonomously and merges its own
  pull requests with `scripts/merge-pr.sh`, **except** when the owner's authorization for the phase says otherwise.
  Outside an approved plan, stop and ask.
- Minor doubts: decide, and list them under "Decisions for review" in the PR description.
- Keep the orchestrating session light: delegate implementation and review per pull request when the runtime
  supports it; otherwise work one pull request at a time and keep each small.
- Final report of a phase to the owner: merged PRs, tests and coverage, every "Decisions for review", and a security
  section. Short reports in the chat; details in the PR descriptions and `docs/plans/phase-N.md` (moved to
  `docs/plans/archive/` once the phase is finished).
- A new session must read, in this order: this file, `docs/plans/phase-N.md` of the current phase (when it exists),
  the last merged PRs (`git log`, `gh pr list --state all --limit 10`). Read other docs per section 2.

## 6. Commands

```bash
# backend (from backend/)
uv sync                                         # create .venv from uv.lock
uv run pytest                                   # tests (incl. import-contract canaries)
uv run pytest --cov                             # + coverage gate: domain + application >= 90% (as in CI)
uv run ruff check . && uv run ruff format --check .
uv run mypy                                     # strict, covers src/, tests/, devtools/
uv run lint-imports                             # import contracts
```

CI (`.github/workflows/ci.yml`) runs the same checks with `uv sync --locked`, so a stale `uv.lock` fails the
build. From `web/`, run `pnpm install --frozen-lockfile`, `pnpm run typecheck`, `pnpm run build`,
`pnpm run test:component` (CI repeats it with `TZ=America/Sao_Paulo`) and `pnpm run test:e2e`. Browser tests use the pinned Chromium from
`pnpm exec playwright install chromium` and E2E uses the locked backend installation.
