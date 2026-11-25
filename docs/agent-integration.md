# Connecting agents

Cérebro exposes four MCP read tools. Start with `cerebro_overview` once per session, search summaries, then read
a selected section or follow a link. Each agent has its own bearer token. For access from another host use
the private HTTPS URL supplied by Tailscale Serve. Loopback HTTP below is for a development instance on this host.

## Automated acceptance

From the application repository in WSL, run:

```bash
cd backend
uv sync --locked
uv run --locked pytest -q tests/integration/test_phase3_acceptance.py tests/integration/test_mcp_transports.py
```

The official SDK exercises the installed HTTP server and local stdio bridge with synthetic public, internal and
restricted notes. The tests cover discovery, visible results, identical hidden/missing responses, restart and
credential revocation. The following manual checks use the same fixture.

## Manual check in Claude Code and Codex

Use one Bash session from the repository's `backend/` directory, with the dev dependencies installed and both
clients available and signed in. The setup creates a temporary private instance and a separate client working
directory. Use process-local client settings for the duration of the check. The CLI options below were checked
against Claude Code 2.1.332 and Codex CLI 0.171.0-alpha.13.2. Record the versions and outcomes of your run.

### 1. Create the fixture and start the private listener

```bash
bash --noprofile --norc
set -euo pipefail
set +x
umask 077
for key in ${!CEREBRO_@}; do unset "$key"; done
bin="$(pwd)/.venv/bin"
session="$(mktemp -d)"
mkdir "$session/client"
config="$("$bin/python" -m devtools.mcp_sample "$session/instance")"
claude_secret="$("$bin/cerebro" --config "$config" token issue claude-manual --ceiling internal)"
codex_secret="$("$bin/cerebro" --config "$config" token issue codex-manual --ceiling internal)"
cd "$session/client"

# Ask the OS for an available loopback port and pass it to the server and both clients.
port="$("$bin/python" -c 'import socket
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    print(s.getsockname()[1])')"
export CEREBRO_DEV_URL="http://127.0.0.1:$port/mcp"
export NO_PROXY="127.0.0.1,localhost" no_proxy="127.0.0.1,localhost"
server_pid=""
stop_server() {
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    server_pid=""
  fi
}
trap 'stop_server' EXIT
start_server() {
  "$bin/cerebro-mcp" --config "$config" --private-port "$port" &
  server_pid=$!
  for attempt in {1..100}; do
    kill -0 "$server_pid" 2>/dev/null || return 1
    if [[ "$(curl --max-time 1 -s -o /dev/null -w '%{http_code}' "$CEREBRO_DEV_URL")" == 401 ]]; then
      kill -0 "$server_pid"
      return
    fi
    sleep 0.1
  done
  return 1
}
start_server
claude --version
codex --version
```

### 2. Run the four tools in each client

The quoted JSON keeps the variable name in the temporary file. Claude Code expands the header at launch.
Codex reads its bearer from the named environment variable. Each command receives just its own fixture token.
[Claude Code MCP configuration](https://code.claude.com/docs/en/mcp#environment-variable-expansion-in-mcp-json)
· [Codex MCP configuration](https://developers.openai.com/codex/mcp)

```bash
cat > "$session/client/.mcp.json" <<'JSON'
{"mcpServers":{"cerebro":{"type":"http","url":"${CEREBRO_DEV_URL}","headers":{"Authorization":"Bearer ${CEREBRO_MCP_TOKEN}"}}}}
JSON
run_claude() {
  CEREBRO_MCP_TOKEN="$claude_secret" claude --print --no-session-persistence \
    --tools "" --allowedTools 'mcp__cerebro__*' --strict-mcp-config --mcp-config "$session/client/.mcp.json" -- "$1"
}
run_codex() {
  CEREBRO_MCP_TOKEN="$codex_secret" codex exec --ephemeral --ignore-user-config --skip-git-repo-check --sandbox read-only \
    -c "mcp_servers.cerebro.url=\"$CEREBRO_DEV_URL\"" \
    -c 'mcp_servers.cerebro.bearer_token_env_var="CEREBRO_MCP_TOKEN"' \
    -c 'mcp_servers.cerebro.default_tools_approval_mode="approve"' "$1"
}
check='Use only Cérebro MCP tools. Call cerebro_overview, then cerebro_search with query SSH.
Call cerebro_read with id server-ssh-access and section SSH. Call cerebro_neighbors with that id and depth 2.
Finally call cerebro_read separately on vault-layout and absent-note. Report the returned core id, search hit,
section content, a two-hop neighbor, and whether both final tool errors are identical.
If tools are unavailable, say “Cérebro offline” and continue without guessing. Never use shell or file tools.'
run_claude "$check"
run_codex "$check"
```

In each client, confirm four distinct read tools were called. Expect `core-rules` in the overview,
`server-ssh-access` in search, “workstation key” in the SSH section and `core-rules` reachable through
`docker-conventions` at depth two. Both final reads return `Not found.` The fixture includes an encrypted
`vault-layout` note and both tokens have the `internal` ceiling.

### 3. Stop and restart, then clean up

```bash
stop_server
offline='Call cerebro_overview. If unavailable, report “Cérebro offline” and continue by replying “Ready for another task.” Do not use other tools.'
run_claude "$offline"
run_codex "$offline"
start_server
run_claude "$check"
run_codex "$check"
stop_server
cd /
rm -rf -- "$session"
exit
```

Record the two offline messages and the first successful read after restart for each client. A stopped HTTP
listener cannot send a protocol response. The prompt tells the real agent how to report that connection failure.
The stdio development bridge can return the fixed offline error directly. Its command is
`cerebro-mcp-stdio --private-port PORT` with the token in `CEREBRO_MCP_TOKEN` in the bridge environment.
Phase 4 adds proposal submission.
