# Connecting agents

Cérebro exposes four read tools to tokens with the `read` scope and `cerebro_propose` to tokens with
the `propose` scope. Start with `cerebro_overview` once per session, search summaries, then read
a selected section or follow a link. Each agent has its own bearer token. For access from another host use
the private HTTPS URL supplied by Tailscale Serve. Loopback HTTP below is for a development instance on this host.

## Automated acceptance

From the application repository in WSL, run:

```bash
cd backend
uv sync --locked
uv run --locked pytest -q tests/integration/test_phase3_acceptance.py tests/integration/test_mcp_transports.py \
  tests/integration/test_phase4_acceptance.py
```

The official SDK exercises the installed HTTP server and local stdio bridge with synthetic public, internal and
restricted notes. The tests cover discovery, visible results, identical hidden/missing responses, restart and
credential revocation. The Inbox test submits a valid proposal and a synthetic private-key marker, verifies
quota refusals and reads the owner-approved commit and its audit trailers from git. The following manual checks use the same fixture.

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

### 3. Propose in both clients and approve locally

Keep the private dev server running. Use fresh propose-only tokens in a subshell so the read checks retain
their original credentials. Each client submits a distinct synthetic note and reports its proposal id.
The token identifies the proposer. The default limits are 20 admissions per rolling hour and 10 pending
proposals per token. Either limit returns `Proposal limit reached.`.

```bash
(
  cli=("$bin/cerebro" --config "$config")
  claude_secret="$("${cli[@]}" token issue claude-inbox --scope propose --ceiling internal)"
  codex_secret="$("${cli[@]}" token issue codex-inbox --scope propose --ceiling internal)"
  for client in claude codex; do
    "run_$client" "Call cerebro_propose exactly once using these JSON arguments:
{\"kind\":\"create\",\"title\":\"$client inbox sample\",\"body\":\"Synthetic $client proposal for owner review.\",
 \"suggested\":{\"type\":\"guide\",\"area\":\"infrastructure\",\"priority\":\"P2\",\"sensitivity\":\"internal\",
 \"hub\":\"infrastructure\",\"summary\":\"A synthetic guide used to check the local Inbox approval workflow.\"}}
Report the returned proposal_id and valid flag. Use no other tools."
  done
)
```

Both results should have `valid: true`. The owner reviews each candidate from the same Bash session. Copy
the corresponding proposal id from each client result when prompted. Check the source agent, candidate body
and suggested target in the displayed preview before confirming each revision.

```bash
cli=("$bin/cerebro" --config "$config")
repo="$session/instance/content"
"${cli[@]}" inbox list
for agent in claude-inbox codex-inbox; do
  read -rp "Proposal id returned for $agent: " pid
  preview="$("${cli[@]}" inbox list --id "$pid")"
  printf '%s\n' "$preview"
  revision="$("$bin/python" -c '
import json, sys
p = json.load(sys.stdin)
if p["source_agent"] != sys.argv[1] or p["status"] != "needs_owner":
    raise SystemExit("Unexpected source agent or proposal status")
print(p["revision"])' "$agent" <<<"$preview")"
  read -rp "Approve the displayed revision $revision for $agent? [y/N] " answer
  if [[ "$answer" != y ]]; then continue; fi
  if approval="$("${cli[@]}" inbox approve "$pid" --revision "$revision")"; then
    printf '%s\n' "$approval"
    commit="$("$bin/python" -c 'import json, sys; print(json.load(sys.stdin)["commit"])' <<<"$approval")"
    git -C "$repo" log -1 --format=%B "$commit" --
  else
    printf 'Approval failed for %s. Inspect the latest preview before retrying.\n' "$pid" >&2
  fi
done
```

Each approval returns `applied` and creates one commit with `Proposed-by: claude-inbox` or
`Proposed-by: codex-inbox`, `Curated-by: curator/null`, `Approved-by: owner` and the displayed proposal
id in `Proposal-Id`. The new note ids are `claude-inbox-sample` and `codex-inbox-sample`.

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

## Phase 7 external curator

**Planned manual acceptance, pending implementation.** Run this section in WSL after §§1–3 and **before §4** closes the fixture shell. Use both signed-in clients on the private loopback listener. The functions from §2 use process-local MCP settings and inject only the selected fixture bearer. This check needs no provider API key or global client configuration. Use the temporary synthetic instance only.

Submit two independent proposals using propose-only credentials. Each curator will review a proposal from a different identity. Keep the proposal IDs from the two receipts.

```bash
cli=("$bin/cerebro" --config "$config")
repo="$session/instance/content"
before="$(git -C "$repo" rev-parse HEAD)"
(
  claude_secret="$("${cli[@]}" token issue sample-inventory-author --scope propose --ceiling internal)"
  codex_secret="$("${cli[@]}" token issue sample-drill-author --scope propose --ceiling internal)"
  run_claude 'Use only cerebro_propose. Submit exactly once with these arguments:
{"kind":"create","title":"Inventory spare network cables","body":"Label each spare cable with length and connector type. Record its cabinet slot when returning it.",
"suggested":{"type":"guide","area":"infrastructure","priority":"P2","sensitivity":"internal","hub":"infrastructure",
"summary":"How to label and locate spare network cables in the infrastructure cabinet."}}
Report proposal_id and valid.'
  run_codex 'Use only cerebro_propose. Submit exactly once with these arguments:
{"kind":"create","title":"Verify an offline restore drill","body":"Restore the synthetic archive into an empty scratch directory. Compare its fixture manifest and record the drill date.",
"suggested":{"type":"guide","area":"infrastructure","priority":"P2","sensitivity":"internal","hub":"infrastructure",
"summary":"How to check a sample restore against its manifest and record the drill outcome."}}
Report proposal_id and valid.'
)
read -rp 'Valid cable inventory proposal id from Claude Code: ' inventory_pid
read -rp 'Valid restore drill proposal id from Codex: ' drill_pid
```

Proceed only if both receipts show `valid: true`. Give each client a separate curate-only token and the other client's proposal ID. Each run must call all four tools in order. The server returns the [curator guide](curator-guide.md) as the first tool result.

```bash
(
  claude_secret="$("${cli[@]}" token issue claude-curator --scope curate --ceiling internal)"
  codex_secret="$("${cli[@]}" token issue codex-curator --scope curate --ceiling internal)"
  curate_sample() {
    "$1" "Use only Cérebro curator MCP tools. Call cerebro_curator_guide and read its version and instructions.
Call cerebro_curator_list and locate proposal $2. If absent, stop. Fetch cerebro_curator_context for that item.
Review it using only the returned context. For this distinct synthetic guide, recommend create if justified.
Call cerebro_curator_decide with the guide version, inspected revision, context_id and complete English candidate
required by the schema. Stop on a conflict. Report guide version, proposal ID and decision outcome.
Do not print credentials or context_id, use shell tools, or attempt owner approval."
  }
  curate_sample run_claude "$drill_pid"
  curate_sample run_codex "$inventory_pid"
)
test "$(git -C "$repo" rev-parse HEAD)" = "$before"
```

Inspect the owner preview for each ID. Compare the original with the complete English candidate and its diff, including the target, summary, area, hub and sensitivity. Verify the curator and proposer identities against the table below. Approve only the inspected revision. A stale review needs a fresh preview.

| Proposal | Proposed by | Expected curator |
|---|---|---|
| Restore drill | `sample-drill-author` | `curator/claude-curator` |
| Cable inventory | `sample-inventory-author` | `curator/codex-curator` |

```bash
for pid in "$drill_pid" "$inventory_pid"; do
  preview="$("${cli[@]}" inbox list --id "$pid")"
  printf '%s\n' "$preview"
  revision="$("$bin/python" -c 'import json,sys; print(json.load(sys.stdin)["revision"])' <<<"$preview")"
  read -rp "Approve the displayed candidate at revision $revision? [y/N] " answer
  if [[ "$answer" != y ]]; then continue; fi
  approval="$("${cli[@]}" inbox approve "$pid" --revision "$revision")"
  printf '%s\n' "$approval"
  commit="$("$bin/python" -c 'import json,sys; print(json.load(sys.stdin)["commit"])' <<<"$approval")"
  git -C "$repo" show --format=%B "$commit" --
done
```

For each approval, compare the committed change with the preview. Check the two identity trailers above, `Approved-by: owner` and the matching `Proposal-Id`. Record client versions, guide version, both decision outcomes, the unchanged pre-approval HEAD check and the two approval results. Continue with §4 to shut down and remove the temporary instance.
