# Connecting agents

Cérebro exposes four tools to tokens with `read`. Tokens with `write` can call `cerebro_write` and
`cerebro_write_guide`, which returns the writing rules with the content repository's areas and note types. Issue both scopes for an agent that searches and maintains notes. Core and P0 notes
are the global rules every agent receives in the overview; creating, editing, archiving or superseding one
also needs the `core` scope ([ADR-0022](adr/0022-core-scope.md)). Grant `core` only to agents you trust to
change those rules, such as your own Claude Code and Codex sessions, and always together with `write`
(`token issue <agent> --scope read --scope write --scope core`). Hubs (P1) and regular notes need only
`write`. Start with `cerebro_overview` once per session, search summaries, then read
a selected section or follow a link. Each agent has its own bearer token. For access from another host use
the public URL and a bearer header as in [client setup](client-setup.md). Loopback HTTP below is for a development instance on this host.

## Project AGENTS.md snippet

```markdown
Use Cérebro for durable project context. Start a session with `cerebro_overview`.
Search before creating a note and prefer updating an existing one.
When you have something worth recording, read `cerebro_write_guide` and follow it.
```

## Automated acceptance

From the application repository in WSL, run:

```bash
cd backend
uv sync --locked
uv run --locked pytest -q tests/integration/test_phase3_acceptance.py tests/integration/test_mcp_transports.py \
  tests/integration/test_phase7_acceptance.py tests/integration/test_write_guide_distribution.py
```

The official SDK exercises the installed HTTP server and local stdio bridge with synthetic public, internal and
restricted notes. The tests cover discovery, visible results, identical hidden/missing responses, restart and
credential revocation. The direct-write test creates through HTTP and updates through stdio, checks replay, stale hashes,
secret and quota rejections and inspects the two attributed Git commits. The distribution test loads the guide
from an installed wheel. The following manual checks use the same fixture.

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
server_pid=""
stop_server() {
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    server_pid=""
  fi
}
trap 'stop_server; cd /; rm -rf -- "$session"' EXIT
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
```

Run the read check after defining both functions.

```bash
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

### 3. Create in Claude Code and update in Codex

The read tokens from step 1 authorize four tools. Issue a fresh `read` + `write` token to each client in
the subshell below. Both should discover two additional tools, `cerebro_write_guide` and `cerebro_write`.
Each prompt asks the client to consult the guide before writing. The working fixture is disposable.

```bash
(
  cli=("$bin/cerebro" --config "$config")
  repo="$(dirname -- "$config")/content"
  claude_secret="$("${cli[@]}" token issue claude-writer --scope read --scope write --ceiling internal)"
  codex_secret="$("${cli[@]}" token issue codex-writer --scope read --scope write --ceiling internal)"
  before="$(git -C "$repo" rev-parse HEAD)"
  run_claude 'Use only Cérebro MCP tools. Read cerebro_write_guide and search for "fixture handover".
If the fixture handover note already exists, stop. Otherwise call cerebro_write with these arguments:
{"operation":"create","request_id":"manual-handover-1","body":"Record the active revision before handover.",
 "frontmatter":{"title":"Fixture handover","type":"guide","area":"infrastructure","priority":"P2",
 "sensitivity":"internal","hub":"infrastructure","summary":"How to record the active revision during a synthetic handover."}}
Read back the returned note_id. Report the id, revision and body.'
  create_rev="$(git -C "$repo" rev-parse HEAD)"
  test "$(git -C "$repo" rev-list --count "$before..$create_rev")" = 1
  test "$(git -C "$repo" log -1 '--format=%(trailers:key=Written-by,valueonly,separator=%x2C)')" = claude-writer
  run_codex 'Use only Cérebro MCP tools. Read cerebro_write_guide, search for "fixture handover" and read the
full Fixture handover note. Call cerebro_write to update it using its id as target and the returned base_hash.
Preserve its writable frontmatter. Change the body to "Record and verify the active revision before handover."
Report the update revision. Repeat that exact update once with the same old hash to exercise stale_base.
Report its error code, then read the current note and report the body. Stop after that read.'
  update_rev="$(git -C "$repo" rev-parse HEAD)"
  test "$(git -C "$repo" rev-list --count "$create_rev..$update_rev")" = 1
  test "$(git -C "$repo" log -1 '--format=%(trailers:key=Written-by,valueonly,separator=%x2C)')" = codex-writer
  git -C "$repo" log --format='%h %cI %s%n%(trailers:key=Written-by)' "$before..$update_rev"
  git -C "$repo" diff "$create_rev" "$update_rev" -- '*.md'
)
```

Compare each reported revision with Git. Confirm that both commits name the same note, the diff adds
“and verify” to the body, and the repeated update yields `stale_base` with no third commit. Errors carry
a field and correction hint. On a rate limit, wait for a slot in the token’s rolling hour.

### 4. Stop and restart, then clean up

```bash
stop_server
offline='Call cerebro_overview. If unavailable, report “Cérebro offline” and continue by replying “Ready for another task.” Do not use other tools.'
for client in run_claude run_codex; do
  status=0
  "$client" "$offline" || status=$?
  printf '%s offline exit status: %s\n' "$client" "$status"
done
start_server
run_claude "$check"
run_codex "$check"
exit
```

Record the two offline messages and the first successful read after restart for each client. A stopped HTTP
listener cannot send a protocol response. The prompt tells the real agent how to report that connection failure.
The stdio development bridge can return the fixed offline error directly. Its command is
`cerebro-mcp-stdio --private-port PORT` with the token in `CEREBRO_MCP_TOKEN` in the bridge environment.
The bridge gives up connecting after 5 seconds but waits up to 120 seconds for a reply, longer than a write or an
index rebuild may wait for the writer lock, so a busy server is never reported as offline.

“Cérebro offline” means the server could not be reached. A running server that fails answers with a fixed
**local failure** instead: “Cérebro local failure: some note files need the owner's attention …” when a note file
blocks the request (for example a hand edit that was never committed), or a generic local failure otherwise. Neither
names a path or a note; retrying does not help until the owner fixes the file. The server's standard error
gets one line per failure with the route and the exception class only; the owner finds the files in **Health** or
with `cerebro health`.

## Inspect the same client writes in Recent changes

Use this variant to see both clients' commits in the browser. Start the [WSL web demo](plans/archive/phase-5.md#7-open-the-interface-from-wsl) and keep its terminal open. Open the printed private browser URL, compare the certificate fingerprint and sign in. In a second WSL terminal, from the application repository root, prepare a temporary client workspace:

```bash
bash --noprofile --norc
set -euo pipefail
set +x
umask 077
for key in ${!CEREBRO_@}; do unset "$key"; done
bin="$(git rev-parse --show-toplevel)/backend/.venv/bin"
session="$(mktemp -d)"
trap 'cd /; rm -rf -- "$session"' EXIT
mkdir "$session/client"
cd "$session/client"
read -rp 'Fixture configuration path printed by web_demo: ' config
read -rp 'Private MCP in WSL URL printed by web_demo: ' CEREBRO_DEV_URL
export CEREBRO_DEV_URL
export NO_PROXY="127.0.0.1,localhost" no_proxy="127.0.0.1,localhost"
```

Run the **first Bash block of step 2** (client configuration and functions), then **step 3** (issue write grants and perform the two writes). Use a fresh demo for each run. In the private browser, open **Recent changes**. The two newest entries should name `codex-writer` and `claude-writer`, show their timestamps and link to the same note. Open each diff and compare it with the Git output from step 3. The create shows the initial note; the update adds “and verify”. Follow the note link to read the current version. Exit the second shell, then press Ctrl+C in the demo terminal to remove the fixture and its credentials.
