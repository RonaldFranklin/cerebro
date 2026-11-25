# Vision

## Problem

The owner works on many projects and infrastructure pieces at the same time and uses several AI agents in
parallel (e.g. Claude Code and Codex). Each new session starts without context. Today that context is kept in
ad-hoc Markdown/prompt files that must be copied into every agent and kept in sync by hand. This is costly in
time and in tokens (large prompts loaded whole, every session, whether relevant or not).

## Goal

Cérebro is the **single source of curated context** for every agent and session:

1. Any agent can ask Cérebro for exactly the context it needs, cheaply.
2. Knowledge stays organized and trustworthy as it grows, because writes are controlled.
3. The owner can see and read the whole structure visually without going through an agent.

## Principles

- **Agent-first, human-friendly.** Optimized for LLM consumption (small summaries, bounded sizes,
  progressive disclosure), yet pleasant for a human to browse.
- **Cheap per query, flat as it scales.** A typical lookup should cost roughly 2–3k tokens regardless of how
  many notes exist.
- **Controlled writes.** Agents propose; validation + curator + owner decide.
- **Current over historical.** Superseded knowledge is kept for history but never served as current.
  Operational knowledge distinguishes *proposed*, *applied* and *verified*.
- **Secure by default.** Sensitivity ceilings per entry point and per token; restricted content encrypted at
  rest outside the server; no secrets ever stored (notes say *where* a secret lives, never its value).
- **Solid and boring.** Hexagonal core with zero third-party dependencies; mature, pinned libraries at the
  edges.
- **Self-hosted, single user.** Whoever runs an instance is its only user.

## Non-goals (for now)

- Multi-user / multi-tenant / sharing between people.
- Storing secrets (passwords, tokens, keys) — explicitly forbidden.
- Translating note content. Everything is English; users rely on browser translation.
- Real-time collaborative editing.
- Running on the owner's workstation. Runtime is server-only; the workstation is for development.
- Offline availability when the server is down. Agents get a clear "Cérebro offline" answer and continue
  without it.

## Success criteria

- Claude Code and Codex on the owner's PC both use Cérebro via MCP with no duplicated instruction files
  beyond a few global lines pointing to it.
- A question like "how do I access the server?" is answered with ≤ 3 tool calls and ≈ 2–3k tokens.
- No note enters the knowledge base without passing validation, and every change is a reviewable git commit.
- The owner can open the web UI, see the graph colored by area and sized by priority, filter it, and read any
  note.
