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
- New data for the screens is designed in [phase-8-data.md](phase-8-data.md), approved by the owner on 2026-11-27 and
  built in the order A and G, F, B, C, D. Token "last used" and 24-hour activity are counters in a separate
  `usage.sqlite` in the state directory and record no request content. The editor has no commit message field.

## Layout decisions (step 2)

- The rail's **Notes** destination is the search screen; the overview stays on `/`, reached through the logo.
- The top bar search starts a new search with default filters. The search screen keeps its own field and filters.
- Controls are bounded by a stronger line than decorative separators, so their edges keep 3:1 contrast.
- Area colors in the graph change with the graph screen (step 3).
- The `/` shortcut is a single character that cannot be remapped, as requested; it never acts inside a field or the
  editor.
- The built UI, and therefore the image, ships each font's OFL text under `licenses/`, copied from its package.

## Graph and sign-in decisions (step 3)

Step 3a (#110): graph and sign-in, implemented and tested.

- Filters keep every former dimension and start at "all", including status.
- A click selects a note; the neighborhood view opens from the panel or the note list, which stays below the graph
  as the keyboard and no-WebGL path.
- Unnamed areas take a palette in sorted order, so a new area can change the color of another unnamed one.
- Labels of notes with live activity and the activity feed belong to step 4.

## Note screen decisions (step 3)

Step 3b: note screen, implemented and tested. The commit history panel waits for part F of
the data design proposal (pull request 109). The former related-notes and details lists are replaced by links in both
directions and a property grid.

## Recent changes and Health decisions (step 3)

Step 3c: both screens with the data that exists, implemented and tested. The superseded badge, last backup,
entrances and the token table wait for the data design. The author filter covers the loaded changes.

## Live synapses (step 4)

1. Design its event together with the usage counters of part C
   ([shared design](phase-8-data.md#shared-design-with-the-live-channel-step-4)).
2. Propose the ADR for the live channel (SSE for the owner session only, filtered by the session ceiling, `connect-src
   'self'`, events with note id, verb and client only, bounded connections, no token logging). Nothing is implemented
   before the owner accepts it.
3. Backend channel, then the pulse on the node or edge and the **Live activity** feed under the graph's note panel,
   with names shown on active notes. Under `prefers-reduced-motion` the highlight is static.

The counters and the live channel follow [ADR-0031](../adr/0031-usage-counters-and-live-activity.md), accepted by the
owner on 2026-11-27.

## Left for later

- Verify the Cloudflare Access assertion (JWT) in the application, so that `restricted` on the public listener does
  not rest on deployment configuration alone. Needs its own ADR (Phase 9).

## Delivery record

| Step | Pull request | State |
|---|---|---|
| 1 | #107 | Merged on 2026-11-27 after owner review |
| 2 | #108 | Merged on 2026-11-27 after owner review. The image build is checked at deployment |
| 3a | #110 | Graph and sign-in: merged on 2026-11-27 |
| 3b | #111 | Note screen: merged on 2026-11-27 |
| 3c | #112, #113 | Recent changes and Health with existing data: merged on 2026-11-27 |
| 3d | #109, #115, #116, #117, #119, #124 | Data design approved. Parts A, G, F, B and C: merged on 2026-11-27, C after owner review |
| 3e | #121 | Edit screen with live validation (part D): merged on 2026-11-27 |
| 4 | #125 | Live synapses: merged on 2026-11-27 after owner review. Not yet checked through a deployed proxy |
