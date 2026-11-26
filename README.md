# Cérebro

**One brain for all your AI agents.**

Cérebro is a self-hosted, single-user knowledge base built for AI agents first and humans second.
You keep your documentation, configurations, architecture decisions and hard-won know-how in one place,
and every agent you use — Claude Code, Codex, ChatGPT or anything that speaks
[MCP](https://modelcontextprotocol.io) — reads the same curated context instead of you pasting the same
Markdown file into every new chat.

> **Status:** Agents can read and write through MCP. The owner can inspect each attributed commit and diff in **Recent changes**. See [`docs/roadmap.md`](docs/roadmap.md).

## Why

If you work on many things at once, context gets scattered: a prompt file here, a README there, a note in
another tool. Every new session with every new agent starts from zero, and keeping several copies of the same
instructions in sync is tedious and error-prone.

Cérebro makes that context a single, structured, versioned source that agents can query cheaply.

## Key ideas

- **Progressive disclosure.** Agents read a tiny core first, then hubs, then only the notes they need.
  Context cost per query stays flat as the knowledge base grows.
- **Priority levels (P0–P3).** What every agent must always know vs. what it reads only on demand.
- **Sensitivity levels.** `public`, `internal`, `restricted`. Each agent token and each network entry point
  has a ceiling; restricted notes are encrypted at rest in the content repository.
- **Authorized agents write through MCP.** Before writing, consult `cerebro_write_guide` and search for an
  existing note. `cerebro_write` validates each create or update and records the authenticated agent in Git.
  Updates use the `base_hash` from a complete read. The owner explicitly issues `read` and `write` grants.
- **Plain files, real history.** Notes are Markdown with YAML frontmatter in a git repository. The search
  index is derived and can be rebuilt at any time.
- **See your knowledge.** A web UI to browse and read notes and explore the knowledge graph.
- **Hexagonal core.** The domain has zero third-party dependencies; storage and transports
  are swappable adapters.

## How it fits together

```
 Your machine                                   Your server
 ┌──────────────────────┐                      ┌──────────────────────────────────┐
 │ Claude Code / Codex  │── MCP (HTTPS) ──────▶│  Cérebro                         │
 │ Browser              │── Web UI ──────────▶│   API · MCP · Validated writes    │
 └──────────────────────┘                      │   Index (SQLite FTS5)            │
                                               │   Content repo (git, private)    │
                                               └──────────────────────────────────┘
```

## Documentation

- [Vision](docs/vision.md)
- [Architecture](docs/architecture.md)
- [Domain model](docs/domain-model.md)
- [Roadmap](docs/roadmap.md)
- [Connecting agents and the three-line project snippet](docs/agent-integration.md#project-agentsmd-snippet)
- [Writing guide](backend/src/cerebro/adapters/inbound/write-guide-v1.md)
- [Architecture Decision Records](docs/adr/)

## License

[Apache License 2.0](LICENSE)

## Local quickstart

From the repository root in Linux or WSL, export the instance variables from `.env.example` with your own
absolute paths. Use a new content directory, a private state directory and a key file outside both repositories.
Run the following in Bash as the service user.

```bash
cd backend
uv sync --locked
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
```

Save the key and owner password in your password manager outside this machine. Set your areas in
`schema/areas.yaml` in the content repository and commit any edits there. In a private terminal, issue
a token and save the printed value for the client. Start the private MCP listener in the same shell.

```bash
uv run cerebro token issue codex --ceiling internal --scope read --scope write
uv run cerebro-mcp
```

From another terminal, verify that an unauthenticated request to `http://127.0.0.1:8765/mcp` returns HTTP 401.
For client configuration, see [Connecting agents](docs/agent-integration.md).
