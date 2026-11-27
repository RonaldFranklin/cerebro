# Cérebro

**One brain for all your AI agents.**

Cérebro is a self-hosted, single-user knowledge base built for AI agents first and humans second. You keep your
documentation, configurations, architecture decisions and know-how in one place, and every agent that speaks
[MCP](https://modelcontextprotocol.io) and can send a bearer token (Claude Code, Codex and others) reads and
updates the same curated context, instead of you pasting the same Markdown file into every new chat.

> **Status:** early software. Agents read and write through MCP, and the owner browses, edits and audits notes in a
> web UI. Deployment acceptance is still in progress; see the [roadmap](docs/roadmap.md).

## Who it is for

- You work with one or more coding agents and keep re-explaining the same context.
- You want to host that knowledge yourself, as plain files with Git history.
- Each instance has one user. There are no teams, roles or sharing.

It is **not** a secrets manager: never store passwords, tokens or keys in notes. Cloud chat connectors that require
OAuth (such as the ChatGPT and claude.ai web connectors) cannot connect yet.

## Key ideas

- **Progressive disclosure.** Agents read a tiny core first, then hubs, then only the notes they need.
- **Priority levels (P0–P3).** What every agent must always know versus what it reads on demand.
- **Sensitivity levels.** `public`, `internal`, `restricted`. Each token and each network entry point has a ceiling;
  restricted notes are encrypted at rest in the content repository.
- **Validated writes.** Every create or update is checked (schema, budgets, links, secret scan) and committed to Git
  with the name of the agent that made it.
- **Plain files, real history.** Notes are Markdown with YAML frontmatter; the search index can be rebuilt any time.

```
 Your machine                                   Your server (or the same machine)
 ┌──────────────────────┐                      ┌──────────────────────────────────┐
 │ Claude Code / Codex  │── MCP ──────────────▶│  Cérebro                         │
 │ Browser              │── Web UI (HTTPS) ───▶│   API · MCP · validated writes   │
 └──────────────────────┘                      │   Index (SQLite FTS5)            │
                                               │   Content repo (git, private)    │
                                               └──────────────────────────────────┘
```

## Quickstart with Docker

This runs one instance on your machine, reachable only from that machine, and connects a first agent.

**You need:** Linux or WSL 2; Docker Engine 28+ with Compose v2, usable without `sudo`; Git; Python 3.10+; `flock`;
and an MCP client. Ports 8765 and 8766 must be free (with Docker Desktop, on Windows too). If one is taken, change
`PRIVATE_PORT` or `PUBLIC_PORT` in step 2 and use your port in the later commands. Run the commands in Bash, as a
regular user (not root), in one terminal. Stop at the first command that fails.

### 1. Get the code and choose where data lives

```bash
git clone https://github.com/RonaldFranklin/cerebro.git
cd cerebro
export CEREBRO_HOME="$HOME/cerebro-instance"   # outside the repository
install -d -m 700 "$CEREBRO_HOME" "$CEREBRO_HOME/prod" "$CEREBRO_HOME/prod"/{content,state,index,secrets}
```

### 2. Configure

`prod` is the environment name (the startup guard accepts `prod` or `hml`). You can use your own commit name and
email. [`.env.example`](.env.example) explains every variable.

```bash
cat > "$CEREBRO_HOME/prod.env" <<EOF
ENVIRONMENT=prod
APP_SHA=$(git rev-parse HEAD)
DATA_DIR=$CEREBRO_HOME/prod
SERVICE_UID=$(id -u)
SERVICE_GID=$(id -g)
PRIVATE_PORT=8765
PRIVATE_ORIGIN=https://localhost:8443
PUBLIC_PORT=8766
PUBLIC_ORIGIN=https://localhost:8444
PUBLIC_CEILING=internal
CEREBRO_GIT_NAME="Cerebro Service"
CEREBRO_GIT_EMAIL=cerebro@example.invalid
EOF
dc() { docker compose --env-file "$CEREBRO_HOME/prod.env" -f deploy/compose.yaml "$@"; }
```

The stack also opens a second, "public" listener on `127.0.0.1:8766`, used by the
[server deployment](docs/deploy.md). Both ports are published on loopback only.

### 3. Build and initialize

```bash
dc build
dc run --rm --no-deps backend cerebro init-content
```

Create the encryption key for restricted notes:

```bash
(umask 077; set -o noclobber
 docker run --rm --network none "cerebro-backend:git-$(git rev-parse HEAD)" python -c \
   'import sys; from cerebro.adapters.outbound.crypto import generate_key; sys.stdout.buffer.write(generate_key() + b"\n")' \
   > "$CEREBRO_HOME/prod/secrets/fernet.key")
```

**Save a copy of `$CEREBRO_HOME/prod/secrets/fernet.key` in your password manager.** Without it, restricted notes
cannot be recovered.

Set the password for the web UI (typed twice, not shown):

```bash
dc run --rm --no-deps backend cerebro owner-password
```

Choose your **areas**, the top-level topics of your knowledge (lowercase words joined by hyphens). Only you can add
them:

```bash
content="$CEREBRO_HOME/prod/content/repo"
printf 'areas:\n  - projects\n  - infrastructure\n' > "$content/schema/areas.yaml"
git -C "$content" -c user.name='Cerebro Service' -c user.email='cerebro@example.invalid' \
  commit -m 'Add areas' -- schema/areas.yaml
```

### 4. Start

```bash
deploy/start.sh "$CEREBRO_HOME/prod.env"
curl -sS -o /dev/null -w 'MCP HTTP %{http_code}\n' http://127.0.0.1:8765/mcp
```

Expect `MCP HTTP 401`: the server is up and refuses requests without a token.

### 5. Connect your first agent

Issue a token for it. `read` searches and reads, `write` records notes, and `core` also allows changing the global
rules every agent receives, so grant `core` only to agents you trust.

```bash
install -d -m 700 ~/.config/cerebro
(umask 077; set -o noclobber
 dc exec -T backend cerebro token issue claude-code --ceiling internal --scope read --scope write --scope core \
   > ~/.config/cerebro/claude-code.token)
```

**Claude Code:**

```bash
claude mcp add-json --scope user cerebro - <<'JSON'
{"type": "http", "url": "http://127.0.0.1:8765/mcp",
 "headersHelper": "printf '{\"Authorization\":\"Bearer %s\"}' \"$(cat ~/.config/cerebro/claude-code.token)\""}
JSON
claude mcp get cerebro
```

Expect `Connected`. For **Codex** and **other clients**, issue a separate token for each and follow
[client setup](docs/client-setup.md) with the URL `http://127.0.0.1:8765/mcp`.

Open a **new** agent session and ask:

> Call `cerebro_overview` and `cerebro_write_guide`. Then create a hub in the `projects` area and one note under it
> describing what I am working on.

Finally, add the [global instructions](docs/client-setup.md#5-add-the-global-instructions) so every session starts
from Cérebro.

### 6. Open the web UI (optional)

The browser login requires HTTPS. Any TLS reverse proxy that serves `https://localhost:8443`, keeps the `Host`
header and forwards to `127.0.0.1:8765` works. For example, take the [Caddy](https://caddyserver.com/) binary out of
its Docker image and run it on your machine (no root needed):

```bash
cid=$(docker create caddy:2.11.8)
docker cp "$cid:/usr/bin/caddy" "$CEREBRO_HOME/caddy" && docker rm "$cid" > /dev/null
printf '{\n  admin off\n  auto_https disable_redirects\n  skip_install_trust\n}\nhttps://localhost:8443 {\n  reverse_proxy 127.0.0.1:8765\n}\n' \
  > "$CEREBRO_HOME/Caddyfile"
"$CEREBRO_HOME/caddy" run --config "$CEREBRO_HOME/Caddyfile" --adapter caddyfile
```

Open `https://localhost:8443`, accept the browser's warning about the local certificate, and sign in with your owner
password. Stop the proxy with Ctrl+C.

### Everyday commands

In a new terminal, go back to the repository and define `CEREBRO_HOME` and the `dc` function again.

```bash
dc exec backend cerebro health               # check content and index
dc exec backend cerebro token list           # token metadata; revoke with: cerebro token revoke <token-id>
dc stop                                      # stop
deploy/start.sh "$CEREBRO_HOME/prod.env"     # start again
dc restart backend                           # needed after editing areas or types
```

To **update**, [back up](deploy/backup.md) first, then `git pull`, set the new `APP_SHA` in `prod.env`, run
`dc build` and `deploy/start.sh` again. To **remove** the instance, run `dc down`; your data stays in
`$CEREBRO_HOME` until you delete it.

## Documentation

| Guide | For |
|---|---|
| [Client setup](docs/client-setup.md) | Claude Code, Codex and other MCP clients |
| [Server deployment](docs/deploy.md) | Test and production environments, private and public HTTPS access |
| [Backup and restore](deploy/backup.md) | Protecting content and state |
| [Configuration](.env.example) | Environment variables |
| [Writing guide](backend/src/cerebro/adapters/inbound/write-guide-v1.md) | What agents should record, and how |
| [Connecting agents](docs/agent-integration.md#project-agentsmd-snippet) | Project snippet and client checks |
| [Vision](docs/vision.md) · [Architecture](docs/architecture.md) · [Domain model](docs/domain-model.md) | How and why it works |
| [Decision records](docs/adr/) · [Roadmap](docs/roadmap.md) | Decisions and plans |
| [Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md) | Changing the code, reporting vulnerabilities |

## License

[Apache License 2.0](LICENSE)
