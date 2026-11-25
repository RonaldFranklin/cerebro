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
- **Release age ≥ 7 days.** Use only versions published at least 7 days ago, as protection against
  compromised releases (which are usually detected and yanked within days). This applies to every pinned
  artifact: Python packages (direct and transitive), the build backend, uv itself, GitHub Actions, and later
  Node, pnpm and npm packages.
  - Python packages are enforced by uv: `[tool.uv] exclude-newer = "7 days"` in `backend/pyproject.toml`
    (stored in `uv.lock` as `exclude-newer-span = "P7D"`). `uv lock` refuses to pick anything newer.
  - Tools and actions that uv cannot check (the uv binary and `tool.uv.required-version`, `uv_build`'s
    `build-system` pin, the uv version in CI, GitHub Action SHAs) are checked by hand before bumping: look up
    the release date (PyPI upload time or GitHub release date) and pin the newest release that is at least
    7 days old.
  - Frontend (Phase 5): use pnpm's equivalent setting (minimum release age), or the same manual check if the
    chosen pnpm version lacks it.
- Runtime versions:
  - Python = newest minor that has been GA for at least 12 months. Chosen and pinned at bootstrap (Phase 0).
  - Node = the Active LTS line current when Phase 5 (web UI) starts. Only this policy is fixed now; the exact
    Node version is chosen, pinned and recorded at the start of Phase 5, not at bootstrap. No Node tooling
    exists before Phase 5.

**Backend:** Python, uv, FastAPI + Uvicorn, official `mcp` SDK, PyYAML, `cryptography`, stdlib `sqlite3`
(FTS5), stdlib `hashlib.scrypt`, git CLI via `subprocess`, Typer or argparse for CLI.
Quality: pytest, pytest-cov (coverage gate), ruff, mypy (strict), import-linter.

**Frontend:** React, TypeScript, Vite, pnpm, TanStack Query, Sigma.js + graphology (+ ForceAtlas2 layout in a
web worker), CodeMirror 6.

## Consequences

- Exact backend versions are checked against current releases at the start of Phase 0 and listed in
  `docs/architecture.md` §7 once the lockfile is generated, so the docs match the lock.
- Exact Node and frontend package versions are checked and listed in `docs/architecture.md` §7 at the start
  of Phase 5, once `pnpm-lock.yaml` is generated.

## Alternatives considered

- TypeScript across the whole stack: one language, but Python fits the owner's background and the
  data-oriented tooling.
- D3/SVG graph rendering: too slow for thousands of nodes.
