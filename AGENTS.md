# AGENTS.md — Cérebro (application repository)

Instructions for coding agents (Claude Code, Codex, and others) working on **this repository**.
Claude Code reads this file automatically when no `CLAUDE.md` exists; keep it that way so one file serves every agent.

> This repo holds the **application** code. The knowledge itself (the notes) lives in a separate, private
> **content repository**. Never add real notes, personal data or infrastructure details here: this repo is public.

## 1. What Cérebro is

A self-hosted, single-user knowledge base that gives every AI agent (Claude Code, Codex, ChatGPT, …) the same
curated, prioritized context through MCP, plus a web UI where the owner browses notes and sees the knowledge graph.
Agents **read** freely (within their sensitivity ceiling) but **never write directly**: they submit proposals to an
Inbox, which are validated, curated and approved before becoming notes.

## 2. Read before working (in this order)

1. `docs/vision.md` — goals, non-goals, success criteria.
2. `docs/architecture.md` — hexagonal layout, layers, ports, runtime topology.
3. `docs/domain-model.md` — note schema, priorities, sensitivity, statuses, proposals.
4. `docs/roadmap.md` — phases and acceptance criteria. **Work only on the current phase.**
5. `docs/adr/` — accepted decisions. Do not contradict an ADR; propose a new ADR that supersedes it instead.

## 3. Current status

- Phase 0 (bootstrap) is **done**: `backend/` skeleton with uv (Python 3.14), pytest, ruff, mypy `--strict`,
  import-linter (layers + custom `stdlib_only` contract type, with canary tests) and GitHub Actions CI.
- Phase 1 (domain and application core) is **in progress**, delivered as a series of pull requests
  (`phase-1/*` branches). Done so far: value objects, validation, frontmatter boundary and budgets (PRs 1–2), links, sections
  and cross-note rules (PR 3), secret scanner (PR 4).
- Update this section and `docs/roadmap.md` whenever a phase starts or finishes.
- Phase 1 plan (pull requests, status, design decisions): [`docs/plans/phase-1.md`](docs/plans/phase-1.md).

## 4. Non-negotiable rules

**Architecture**
- Dependencies point inward: `adapters → application → domain`. Never the other way around.
- `cerebro.domain` uses **only the Python standard library**. No PyYAML, no Pydantic, no FastAPI, nothing.
- `cerebro.application` depends only on `cerebro.domain` and defines ports as `typing.Protocol`.
- Third-party libraries are imported **only** inside `cerebro.adapters.*` and `cerebro.bootstrap`.
- These rules are enforced by `import-linter` in CI. A failing contract is a failing build.

**Write path**
- No inbound adapter may write notes directly. Every change goes through the `SubmitProposal` →
  `ValidateProposal` → `CurateProposal` → `ApplyProposal` use cases, or through `OwnerEditNote` for the owner.
- Every applied change is a git commit in the content repository with `Proposed-by:`, `Curated-by:`,
  `Approved-by:` trailers.

**Security**
- Never commit secrets, tokens, keys, `.env` files or real personal data. Use `.env.example` with placeholders.
- No hostnames, IPs, domains or tailnet names of any real deployment in this repo. Everything is configuration.
- The effective sensitivity ceiling of a request is `min(listener_ceiling, token_ceiling)`. Never derive the
  listener from request headers.

**Language**
- Code, identifiers, comments, docs, commit messages and UI text: **English**.
- The maintainer usually chats in Brazilian Portuguese: answer him in pt-BR in conversation, but keep every
  artifact in English.

## 5. How to work

- Small, verifiable steps. One phase at a time; within a phase, one use case or adapter at a time.
- **Git workflow:** one branch per step (e.g. `phase-1/value-objects`, `docs/<topic>`), never commit to `main`
  directly. Commits follow Conventional Commits (`feat:`, `fix:`, `docs:`, `build:`, `ci:`, `chore:`, `test:`,
  `refactor:`), one commit per step plus review fixes. For every pull request:
  1. open it with `gh pr create` and wait for CI with `gh pr checks <n> --watch`; it must be green;
  2. run a review with a **fresh** sub-agent (or a separate session) that reads only the diff and the relevant
     docs/ADRs and reports problems. Reviewer sub-agents use **Sonnet**, except for pull requests that touch
     security (access ceiling, tokens, encryption, the secret scanner), which use **Opus**;
  3. fix what the review reports, push, and wait for CI again;
  4. merge with `gh pr merge <n> --squash --delete-branch`.
- **Do not merge — stop and hand over to the owner** when the pull request:
  - creates or changes an ADR, or changes rules in `docs/architecture.md` or `docs/domain-model.md`;
  - touches security: access ceiling, tokens, encryption, the secret scanner;
  - adds a new dependency (runtime or dev);
  - has a review finding that was not resolved.
  In that case, leave the pull request open with CI green and report what the owner should review.
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
build. Web commands (`pnpm …`) are added in Phase 5.

## 7. Repository map (target)

```
cerebro/
├── AGENTS.md
├── README.md
├── LICENSE                  # Apache-2.0
├── backend/                 # Python: domain, application, adapters, bootstrap
├── web/                     # React + TypeScript + Vite UI
├── deploy/                  # Dockerfiles, compose files, examples (no real values)
└── docs/
    ├── vision.md
    ├── architecture.md
    ├── domain-model.md
    ├── roadmap.md
    ├── agent-integration.md
    └── adr/
```
