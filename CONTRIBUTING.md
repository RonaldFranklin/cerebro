# Contributing

Thanks for your interest. Cérebro is a small, single-maintainer project: for anything larger than a bug fix, open an
issue first so we can agree on it. Report security problems through the [security policy](SECURITY.md).

## Setup

You need Linux or WSL 2, Git and [uv](https://docs.astral.sh/uv/) 0.13.9 or newer. The web UI also needs Node
26.13.1 and pnpm 12.22.0. Some tests use Docker with Compose.

From `backend/`:

```bash
uv sync --locked
uv run pytest --cov            # coverage gate: domain and application >= 90%
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run lint-imports
```

From `web/`:

```bash
pnpm install --frozen-lockfile
pnpm exec playwright install chromium
pnpm run typecheck
pnpm run build
pnpm run test:component
pnpm run test:e2e
```

## Run a development instance from source

Export the variables of sections "Both" and "B" of [`.env.example`](.env.example) with your own absolute paths. Use
a new content directory, a private state directory and a key file outside both repositories, in a secrets directory
that other users cannot replace. From `backend/`, in Bash:

```bash
(
set -eu
uv run cerebro --check-config
uv run cerebro init-content
uv run python - <<'KEY'
import os
import stat
from pathlib import Path
from cerebro.adapters.outbound.crypto import generate_key

os.umask(0o077)
path = Path(os.environ["CEREBRO_KEY_FILE"])
path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
info = path.parent.lstat()
if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
    raise SystemExit("Choose a private secrets directory owned by the service user (0700).")
with path.open("xb") as key:
    key.write(generate_key() + b"\n")
KEY
uv run cerebro owner-password
)
```

Add your areas to `schema/areas.yaml` in the content repository and commit the file there. In a private terminal,
issue a token, save the printed value for your client, and start the private listener:

```bash
uv run cerebro token issue codex --ceiling internal --scope read --scope write
uv run cerebro-mcp
```

An unauthenticated request to `http://127.0.0.1:8765/mcp` returns HTTP 401.
[Connecting agents](docs/agent-integration.md) also has a ready-made fixture with sample content.

## Rules

- Read [`AGENTS.md`](AGENTS.md), which guides people and coding agents, and the
  [architecture](docs/architecture.md). Dependencies point inward; the domain and application layers use only the
  standard library.
- Do not contradict a [decision record](docs/adr/); propose a new one.
- A new dependency needs a decision, an exact pin and a release at least seven days old.
- One focused change per pull request, with tests, [Conventional Commits](https://www.conventionalcommits.org/) and
  English text.
- Never commit secrets, real notes, personal data or details of a real deployment.

Contributions are licensed under the [Apache License 2.0](LICENSE).
