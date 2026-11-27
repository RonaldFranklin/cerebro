# Phase 8: owner UI on the public entrance and visual redesign

Approved by the owner on 2026-11-27, with mockups. Each step is its own pull request (or a few small ones), with an
independent review, and keeps backend and browser tests green.

| Step | Scope | Owner review before merge |
|---|---|---|
| 1. Public owner UI | Opt-in `--public-browser-ceiling restricted` (default `internal`) gives the signed-in owner ceiling `restricted`, edits, Recent changes and Health on the public listener ([ADR-0029](../adr/0029-owner-browser-full-access-on-public-listener.md)). No visual change. MCP, tokens and the private listener are unchanged. | ADR and security |
| 2. Theme and layout | Dark theme tokens, self-hosted Space Grotesk, Inter and JetBrains Mono, top bar with search and ceiling badge, icon rail, accessibility (4.5:1 contrast, 44 px targets, visible focus, stacked panels on narrow screens). | Font files are a new dependency |
| 3. Screens | Graph, Note, Edit, Recent changes, Health and Sign in, following the mockups. | As the changes require |
| 4. Live synapses | Agent reads and writes through MCP pulse in the graph and appear in an activity feed. Needs an ADR for the live channel: owner session only, filtered by the session ceiling, note id, verb and client only, bounded connections, no token logging, static highlight under `prefers-reduced-motion`. | ADR and security |

## Design decisions

- The graph is a free, globe-like force layout (Sigma.js and ForceAtlas2). There is no brain silhouette and no
  layout toggle.
- Data in the mockups is illustrative. No simulated-data mode is built.
- No new dependency without owner review ([ADR-0013](../adr/0013-stack-and-dependency-policy.md)).
- Fonts, approved by the owner on 2026-11-27: `@fontsource-variable/space-grotesk`, `inter` and `jetbrains-mono`
  5.3.0, importing only the `latin` subset.
- Health additions (last backup, token table): their design is proposed to the owner before implementation. Token
  "last used" and 24-hour activity are counters in the state database and record no request content.

## Left for later

- Verify the Cloudflare Access assertion (JWT) in the application, so that `restricted` on the public listener does
  not rest on deployment configuration alone. Needs its own ADR (Phase 9).

## Delivery record

| Step | Pull request | State |
|---|---|---|
| 1 | #107 | Implemented, tested and independently reviewed; awaiting owner review |
