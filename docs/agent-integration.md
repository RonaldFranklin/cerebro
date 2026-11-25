# Connecting agents

> Draft. Exact configuration commands are verified in Phase 3 against the current versions of each tool.

Cérebro is consumed through MCP over HTTPS. Each agent gets its **own token** (`cerebro token issue <name>`).

## 1. Register the MCP server

### Claude Code

```bash
claude mcp add --transport http --scope user cerebro https://<cerebro-host>/mcp \
  --header "Authorization: Bearer <token>"
```

### Codex

Add a remote MCP server entry to the Codex configuration (`~/.codex/config.toml`) pointing to
`https://<cerebro-host>/mcp` with the bearer token. If the installed Codex version lacks remote HTTP MCP
support, use a stdio→HTTP bridge command instead (documented in Phase 3).

## 2. Global instructions

Add a few lines to each agent's **user-level** instruction file so every session knows Cérebro exists.

- Claude Code: `~/.claude/CLAUDE.md`
- Codex: `~/.codex/AGENTS.md`

Suggested text:

```markdown
## Cérebro (knowledge base)
- Before asking me about my infrastructure, projects, conventions or past decisions, call `cerebro_overview`
  once, then `cerebro_search` / `cerebro_read` as needed. Prefer what Cérebro says over assumptions.
- When you finish something worth remembering (a configuration applied, a decision, a procedure that
  worked), submit it with `cerebro_propose` and a short rationale. Never include secrets.
- If Cérebro is offline, continue without it and tell me.
```

## 3. Cost discipline

- Call `cerebro_overview` once per session.
- Search returns summaries only; read full notes only when needed, preferably a single section.
