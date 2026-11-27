# Connect an agent to Cérebro

An agent needs two things: the environment's public URL and a bearer token issued for that client. No tailnet or
VPN is involved. Each token belongs to one client and one environment, and its ceiling alone decides which notes the
client sees ([ADR-0028](adr/0028-token-decides-public-access.md)). The owner issues `read`, `write` and `core` only
to trusted clients such as their own Claude Code and Codex. `core` lets a client change the global rules every agent
receives. Use those credentials only in projects whose code, instructions, hooks and plugins the owner trusts. Give
other clients `read`, plus `write` if they should record notes, never `core`. Keep tokens in the owner's password
manager and never in a file inside a repository.

The commands below are for Bash on Linux or WSL. Replace `<public-hostname>` with the environment's hostname.

**Same machine as the server** (the [README quickstart](../README.md#quickstart-with-docker)): use
`http://127.0.0.1:<private-port>/mcp` in place of `https://<public-hostname>/mcp`. Skip the `https://` check in
step 1 but still set `CEREBRO_MCP_URL`, and in the token test of section 4 replace `--proto '=https'` with `--proto '=http'`. Plain HTTP is acceptable
only on this loopback address; a token must never cross a network without TLS. Each client needs its own token: issue
one for every client you set up below.

## 1. Check the URL and store the token

```bash
CEREBRO_MCP_URL='https://<public-hostname>/mcp'
curl --proto '=https' -sS -o /dev/null -w 'MCP HTTP %{http_code}\n' "$CEREBRO_MCP_URL"
```

Always use the `https://` URL. Expect `401`. A `302` to `cloudflareaccess.com` means the Access bypass for `/mcp` is
missing. Stop on certificate or DNS errors. Save each client's token in a private file outside every repository. The
hidden prompt keeps it out of shell history:

```bash
install -d -m 700 ~/.config/cerebro
for client in claude-code codex; do
  (umask 077; IFS= read -rsp "$client token (Enter keeps the current file): " t && [ -n "$t" ] &&
    printf '%s' "$t" > ~/.config/cerebro/$client.token); echo
done
```

**Residual risk:** any command running as your user, including project scripts, hooks and plugins an agent runs, can
read these files, and the tokens work from anywhere on the internet. Use them only in trusted projects and ask the
owner to revoke a token as soon as it may be exposed. A desktop keyring (for example `secret-tool lookup` in place of
`cat`) keeps them off disk but not away from your own processes.

## 2. Claude Code

Register a user-level HTTP server. Its `headersHelper` reads the token at each connection, so the token never
appears in `~/.claude.json`. [Claude Code MCP](https://code.claude.com/docs/en/mcp)

```bash
claude mcp add-json --scope user cerebro - <<'JSON'
{"type": "http", "url": "https://<public-hostname>/mcp",
 "headersHelper": "printf '{\"Authorization\":\"Bearer %s\"}' \"$(cat ~/.config/cerebro/claude-code.token)\""}
JSON
claude mcp get cerebro
```

## 3. Codex

Add the server to `~/.codex/config.toml` (check first that no `[mcp_servers.cerebro]` table exists) and a shell
function that passes the token only to Codex. Codex filters credential-named variables from the commands it runs.
[Codex MCP](https://developers.openai.com/codex/mcp/) · [Codex configuration](https://developers.openai.com/codex/config-reference/)

```bash
cat >> ~/.codex/config.toml <<'TOML'

[mcp_servers.cerebro]
url = "https://<public-hostname>/mcp"
bearer_token_env_var = "CEREBRO_CODEX_TOKEN"
TOML
cat >> ~/.bashrc <<'BASH'
codex() { CEREBRO_CODEX_TOKEN="$(cat ~/.config/cerebro/codex.token)" command codex -c shell_environment_policy.ignore_default_excludes=false "$@"; }
BASH
source ~/.bashrc
```

## 4. Other MCP clients

Configure a Streamable HTTP server at `https://<public-hostname>/mcp` with the header
`Authorization: Bearer <token>`. Field names vary by client; a common shape is below. Prefer the client's
environment-variable expansion or secret store to a literal token, and keep the file outside repositories.

```json
{"mcpServers": {"cerebro": {"type": "http", "url": "https://<public-hostname>/mcp",
  "headers": {"Authorization": "Bearer ${CEREBRO_TOKEN}"}}}}
```

Cérebro refuses requests with an `Origin` header or a query string, so browser-based clients cannot connect. Cloud
chat connectors (ChatGPT, claude.ai) need OAuth, which is not available yet. To test a token without a client:

```bash
curl --proto '=https' -sS -H @<(printf 'Authorization: Bearer %s\n' "$(cat ~/.config/cerebro/claude-code.token)") \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' "$CEREBRO_MCP_URL"
```

## 5. Add the global instructions

Append this block once to **both** `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md`, preserving existing instructions.
If a Codex global `AGENTS.override.md` exists, add the block there too. [Claude Code memory](https://code.claude.com/docs/en/memory) · [Codex instruction discovery](https://developers.openai.com/codex/guides/agents-md/)

```markdown
Use Cérebro for durable project context. Start a session with `cerebro_overview`.
If the server is unreachable, report “Cérebro offline” and continue the task without it.
Search before creating a note and prefer updating an existing one.
When you have something worth recording, read `cerebro_write_guide` and follow it.
```

## 6. Verify

Ask each agent to run `test -z "${CEREBRO_CODEX_TOKEN-}"` in its shell and expect success: no token is inherited
by the commands it runs. Open `/mcp` in each client and confirm that `cerebro` connects and offers six tools. Ask the agent to call
`cerebro_overview` (an empty overview is valid for fresh content) and `cerebro_write_guide`. On `401`, confirm the
token belongs to this client and environment. If a token may be exposed, ask the owner to revoke it at once: until
then it works from anywhere on the internet. [Agent integration and fixture checks](agent-integration.md)
