# Cérebro

**One brain for all your AI agents.**

Cérebro is a self-hosted, single-user knowledge base built for AI agents first and humans second.
You keep your documentation, configurations, architecture decisions and hard-won know-how in one place,
and every agent you use — Claude Code, Codex, ChatGPT or anything that speaks
[MCP](https://modelcontextprotocol.io) — reads the same curated context instead of you pasting the same
Markdown file into every new chat.

> **Status:** design complete, implementation not started. See [`docs/roadmap.md`](docs/roadmap.md).

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
- **Agents propose, they don't write.** External agents submit proposals to an Inbox. Deterministic checks
  and an internal curator agent keep the knowledge base clean; the owner approves.
- **Plain files, real history.** Notes are Markdown with YAML frontmatter in a git repository. The search
  index is derived and can be rebuilt at any time.
- **See your knowledge.** A web UI to browse and read notes and explore the knowledge graph.
- **Hexagonal core.** The domain has zero third-party dependencies; storage, LLM providers and transports
  are swappable adapters.

## How it fits together

```
 Your machine                                   Your server
 ┌──────────────────────┐                      ┌──────────────────────────────────┐
 │ Claude Code / Codex  │── MCP (HTTPS) ──────▶│  Cérebro                         │
 │ Browser              │── Web UI ──────────▶│   API · MCP · Inbox · Curator     │
 └──────────────────────┘                      │   Index (SQLite FTS5)            │
                                               │   Content repo (git, private)    │
                                               └──────────────────────────────────┘
```

## Documentation

- [Vision](docs/vision.md)
- [Architecture](docs/architecture.md)
- [Domain model](docs/domain-model.md)
- [Roadmap](docs/roadmap.md)
- [Connecting agents](docs/agent-integration.md)
- [Architecture Decision Records](docs/adr/)

## License

[Apache License 2.0](LICENSE)
