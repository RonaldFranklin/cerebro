# ADR-0013: Stack and dependency policy

- **Status:** accepted
- **Date:** 2026-11-24

## Context

The owner wants mature libraries in stable versions, used only where needed.

## Decision

**Policy**
- Prefer the standard library. Add a dependency only when it removes real complexity; record why.
- Only stable releases (no alpha/beta/rc). Projects must be actively maintained and widely used.
- Pin exact versions in committed lockfiles (`uv.lock`, `pnpm-lock.yaml`). Upgrade deliberately, one
  dependency at a time, with tests green.
- Runtime versions: Python = newest minor that has been GA for at least 12 months; Node = current Active LTS.
  Decided and recorded at bootstrap.

**Backend:** Python, uv, FastAPI + Uvicorn, official `mcp` SDK, PyYAML, `cryptography`, stdlib `sqlite3`
(FTS5), stdlib `hashlib.scrypt`, git CLI via `subprocess`, Typer or argparse for CLI.
Quality: pytest, ruff, mypy (strict), import-linter.

**Frontend:** React, TypeScript, Vite, pnpm, TanStack Query, Sigma.js + graphology (+ ForceAtlas2 layout in a
web worker), CodeMirror 6.

## Consequences

- Exact versions are checked against current releases at the start of Phase 0 and listed in
  `docs/architecture.md` §7.

## Alternatives considered

- TypeScript across the whole stack: one language, but Python fits the owner's background and the
  data-oriented tooling.
- D3/SVG graph rendering: too slow for thousands of nodes.
