# Connect Claude Code and Codex from WSL

Run these steps on the owner's workstation after the server agent validates the environment. Obtain its private Tailscale Serve HTTPS origin (including a port if supplied) and the two corresponding bearer tokens from the owner. Each token belongs to one client and one environment. Use these credentials only in projects whose code, instructions, hooks and plugins the owner trusts to maintain global rules. The owner issues `read`, `write` and `core` to these trusted clients. Keep the tokens in the owner's password manager.

## Reach the tailnet

**Recommended for Windows 11 22H2 or later with WSL 2:** sign in to Tailscale on Windows and let WSL use the Windows connection through mirrored networking. This keeps one tailnet device identity for the workstation. In PowerShell, run `wsl -l -v` and confirm the client distribution shows version 2. Merge these settings into the existing `[wsl2]` section of `%UserProfile%\.wslconfig`, creating the section if necessary. [Microsoft WSL networking](https://learn.microsoft.com/en-us/windows/wsl/networking#mirrored-mode-networking) · [Tailscale on Windows](https://tailscale.com/docs/install/windows)

```ini
[wsl2]
networkingMode=mirrored
dnsTunneling=true
```

Save work in **all** running WSL distributions before applying the change with `wsl --shutdown` in PowerShell. Reopen the client distribution.

**Alternative:** when Windows cannot provide the route, run Tailscale in WSL 2 as its own tailnet node. Use this option with the Windows Tailscale client disconnected. Follow the Linux installation instructions for the distribution, start the `tailscaled` service, run `sudo tailscale up` and sign in to the intended tailnet. Confirm the WSL device has access to the server's HTTPS port. [Tailscale's WSL 2 instructions](https://tailscale.com/docs/install/windows/wsl2)

In WSL Bash, replace the placeholder with the exact HTTPS origin. Check the address against the owner's handoff before configuring a client.

```bash
CEREBRO_ORIGIN='https://<tailnet-hostname-and-optional-port>'
CEREBRO_MCP_URL="${CEREBRO_ORIGIN%/}/mcp"
curl --proto '=https' --connect-timeout 5 --max-time 10 --silent --show-error \
  --output /dev/null --write-out 'MCP HTTP %{http_code}\n' "$CEREBRO_MCP_URL"
```

Expect `401` without a token. Stop on DNS, certificate or connection errors, redirects, or another status. Confirm the origin, MagicDNS, tailnet access and environment with the owner before sending a token.

## Register the user-level HTTP server

Run once in that WSL shell. First check for an existing server named `cerebro` in each client's configuration. If one already exists, confirm its environment with the owner before changing it. Claude registers a user-owned header command that reads the private temporary file prepared at launch. Codex saves the variable name in its user configuration. Both clients use Streamable HTTP. [Claude Code MCP](https://code.claude.com/docs/en/mcp) · [Codex MCP](https://developers.openai.com/codex/mcp/)

```bash
python3 - "$CEREBRO_MCP_URL" <<'PYTHON' | claude mcp add-json --scope user cerebro -
import json, sys
print(json.dumps({"type": "http", "url": sys.argv[1], "headersHelper": 'cat "$CEREBRO_HEADERS_FILE"'}))
PYTHON
codex mcp add cerebro --url "$CEREBRO_MCP_URL" --bearer-token-env-var CEREBRO_CODEX_TOKEN
```

## Add the global instructions

Append this block once to **both** `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md`, preserving existing instructions. Create either file if necessary. These are per-user files in WSL; projects can supply additional instructions. If a Codex global `AGENTS.override.md` exists, Codex uses it in preference to the global `AGENTS.md`; include the same block in the active override. [Claude Code memory](https://code.claude.com/docs/en/memory) · [Codex instruction discovery](https://developers.openai.com/codex/guides/agents-md/)

```markdown
Use Cérebro for durable project context. Start a session with `cerebro_overview`.
If the server is unreachable, report “Cérebro offline” and continue the task without it.
Search before creating a note and prefer updating an existing one.
When you have something worth recording, read `cerebro_write_guide` and follow it.
```

## Launch and verify each client

Use the matching block each time you launch a client in WSL Bash. Paste that client's token at the hidden prompt. Claude's shell receives only a temporary file path. The file contains the bearer until Claude exits; a forced termination may leave it behind. Codex reads its own bearer at startup and filters credential-named variables from its shell commands. Code running as your Linux user can still access user-readable files. [Codex shell environment policy](https://developers.openai.com/codex/config-reference/#shell_environment_policyignore_default_excludes)

```bash
(
  set +x
  umask 077
  unset cerebro_secret
  IFS= read -rsp 'Claude Code Cérebro token: ' cerebro_secret || exit 1
  printf '\n'
  CEREBRO_HEADERS_FILE=$(mktemp "${XDG_RUNTIME_DIR:-/tmp}/cerebro-headers.XXXXXXXX") || exit 1
  export CEREBRO_HEADERS_FILE
  trap 'rm -f -- "$CEREBRO_HEADERS_FILE"' EXIT
  printf '%s' "$cerebro_secret" | python3 -c 'import json, sys; json.dump({"Authorization": "Bearer " + sys.stdin.read()}, sys.stdout)' > "$CEREBRO_HEADERS_FILE" || exit 1
  unset cerebro_secret
  claude
)
```

```bash
(
  set +x
  IFS= read -rsp 'Codex Cérebro token: ' CEREBRO_CODEX_TOKEN || exit 1
  printf '\n'
  export CEREBRO_CODEX_TOKEN
  exec codex -c shell_environment_policy.ignore_default_excludes=false
)
```

Before running project commands, ask each client to execute `test -z "${CEREBRO_CODEX_TOKEN-}" && test -z "${cerebro_secret-}"` in its shell. Expect success; stop if either credential is inherited. This check prints no secret.

Open `/mcp` in each client. Confirm that `cerebro` connects and offers six tools. Ask the agent to call `cerebro_overview` once and report whether it succeeded. An empty overview is valid for fresh content. To verify write discovery without modifying content, ask it to call `cerebro_write_guide`. If authentication fails, confirm that the launch token belongs to this client and environment. Ask the owner to revoke and reissue any exposed token. [Agent integration and fixture checks](agent-integration.md)
