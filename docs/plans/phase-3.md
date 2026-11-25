# Phase 3 plan — CLI and read-only MCP

Proposed for owner approval. All delivery PRs below are planned. The phase starts after approval of the plan and dependencies.
[Roadmap and acceptance criteria](../roadmap.md#-phase-3--cli-and-read-only-mcp)

## 1. Pull requests

Use the order below. Each branch starts from current main after its dependencies merge.
Status legend: ⬜ not started · 🟦 PR open · ✅ merged.

| # | Branch | Content and verification | Depends on | Status |
|---|---|---|---|---|
| 1 | `docs/phase-3-start` | Record owner adjustments and the CLI, transport and dependency decisions in an ADR. Mark the roadmap and hand-off in progress. | Owner approval | ⬜ |
| 2 | `phase-3/cli-bootstrap` | Add the console entry point, configuration loading and argument limits. Exercise help, invalid arguments and missing configuration through the installed command. | 1 | ⬜ |
| 3 | `phase-3/init-content` | Initialize a private git working copy with core, hubs, notes and schema, an empty area list and the domain's seven initial user types. Test first run, safe repeat, partial initialization and refusal to overwrite an unrelated directory. | 2 | ⬜ |
| 4 | `phase-3/cli-read-maintenance` | Wire search, read (including section), reindex and health to the Phase 2 container. Exercise the installed commands against a temporary content repo and state directory. | 2, 3 | ⬜ |
| 5 | `phase-3/token-management` | Implement owner-only ListAgentTokens over TokenStore.all(), then wire token issue, list and revoke. Test owner/agent authorization, one-time secret output and the metadata allowlist. | 2 | ⬜ |
| 6 | `phase-3/inbox-review` | Add owner-only ListProposals over ProposalStore.find(). Wire inbox list, approve and reject against seeded proposals. Test invalid transitions, conflicts, rejection and retry after approval. | 2, 3 | ⬜ |
| 7 | `phase-3/mcp-dependency` | Pin the approved SDK and any approved direct server dependency, commit the lock and update the architecture version table. Verify imports and both transport entry points with the locked environment. | 1 | ⬜ |
| 8 | `phase-3/mcp-read-tools` | Implement the four ADR-0014 schemas, handlers and explicit output projections. Add summary to NoteRef, the graph index and the application graph DTO. Test discovery, defaults, visible relations and denied reads with injected use cases. | 7 | ⬜ |
| 9 | `phase-3/mcp-transports` | Wire authenticated Streamable HTTP and local dev stdio, with transport byte limits. Run the same tool and authorization contract through both, plus socket-specific ceiling and oversized-stream tests. | 2, 5, 8 | ⬜ |
| 10 | `docs/phase-3-acceptance` | Run both real clients in WSL, verify agent-integration.md, record the security checkpoint, then update this plan, roadmap and AGENTS.md with the observed results. | 3–9 | ⬜ |

Follow AGENTS.md §5 for independent diff review, security review, owner-held PRs and green CI.
Use scripts/merge-pr.sh for each authorized merge. The Phase 2 merge authorization was phase-specific.

## 2. Owner adjustments

Pending phase approval. Record each adjustment here with the affected PR numbers before implementation.

## 3. Design decisions proposed for approval

1. **Local owner CLI.** Use argparse subcommands. Bootstrap reads an explicit TOML file with tomllib and applies
   environment overrides, documents placeholders in .env.example and passes Settings to the existing container.
   The local administrative process runs under the owner's OS account with a restricted-ceiling owner context.
   Give inbound handlers only their use cases and DTOs. Keep init-content as a bootstrap setup operation before
   the container needs its vocabulary. Use the existing hardened git boundary and create only repository scaffolding.
   Keep the Fernet key outside the content repo and document provisioning it before normal startup.
2. **Token and inbox administration.** Print an issued secret once on deliberate issue output. List only id, agent
   name, scopes, ceiling, creation, expiry and revocation metadata. Use an explicit projection even for owner
   output. Default inbox listing to review metadata. Require the full owner ceiling for listing and detail.
   Provide an owner-only preview via inbox list --id for curated and needs_owner proposals, showing the
   candidate change, target, decision and revision. Test that the reviewed revision is the one approved and
   that a stale preview prompts a fresh review. Hide raw unvalidated and rejected payloads.
   Approve a reviewable decision via ReviewProposal (including an explicit owner override when required), then use
   the serialized ApplyProposal. An already approved proposal resumes at apply. Report actual status, conflicts
   and restricted-history warnings. Reject calls ReviewProposal only. Phase 4 adds proposal intake and the complete
   submission-to-commit workflow. Seed Phase 3 tests through the existing lifecycle.
3. **MCP contract.** Register only the following tools with short descriptions and read scope. Keep the full graph
   and owner commands off MCP. Project from ceiling-filtered DTOs. GraphSlice already carries directed edges, but NoteRef and the SQLite graph nodes lack summaries.
   Populate summary from the note on rebuild and incremental writes, bump the disposable index schema, and
   contract-test fake and SQLite results plus automatic rebuild from a Phase 2 index. This keeps the adapter
   independent of storage and avoids loading a note body for each neighbor.
   [ADR-0014](../adr/0014-mcp-tool-surface.md)

   | Tool | Input and result |
   |---|---|
   | cerebro_overview | No arguments. Visible P0 text within the core budget, areas and hub id/title/summary. |
   | cerebro_search | query with optional area, type, priority, status, include_history and limit. Set the MCP default to 5. Return id, title, type, area, priority, status and summary. |
   | cerebro_read | id and optional section. Return frontmatter essentials and the requested body or section with filtered links and hub membership. Hidden and absent ids produce identical not-found responses. |
   | cerebro_neighbors | id and optional integer depth, default 1. Reject values outside 1–2 before dispatch. Use visible directed edges to return linked id, title, summary and relation, preserving the connecting edge at depth 2. |

4. **Transport identity.** Serve /mcp on a private loopback socket for local dev or same-host tailscale serve HTTPS.
   Bootstrap binds the listener and supplies its immutable ceiling (private restricted, public internal).
   Keep the public listener disabled. Exercise its ceiling on a second test socket. Derive no authority from
   forwarding headers or tool arguments. Accept HTTP bearer credentials and a separately provisioned dev token
   from the stdio process environment. Reserve stdout for the stdio protocol. Treat stdio as a private dev
   entry point. Choose stateless HTTP with event replay disabled. Authenticate every HTTP request and
   reauthenticate each tool call with AuthenticateToken, including on established stdio connections. Require
   read scope. Test that reconnecting with a different or revoked credential cannot retrieve earlier results. The resulting context uses min(listener, token). Phase 6 handles deployment and Phase 8 handles
   Cloudflare. Tokens travel off-host only over HTTPS.
   [ADR-0005](../adr/0005-sensitivity-and-access-ceiling.md) · [ADR-0010](../adr/0010-network-access.md)
5. **Bound input before use cases.** Proposed starting limits are 64 KiB per MCP message or CLI structured input,
   1 KiB per UTF-8 query, 256 bytes per identifier, filter or section, and 4 KiB per review reason. Use domain
   constraints wherever tighter. Bound every accepted field, including decision overrides, and reject unknown fields.
   Enforce total bytes on the actual HTTP stream (also without Content-Length) and on stdio frames before decoding.
   Check CLI text before dispatch and bound any file/stdin read. Phase 4 must apply the same envelope and explicit
   field caps when it introduces proposal intake. Test exact bounds and one byte over with multibyte strings,
   malformed input and spies showing zero use-case calls, including authentication, for oversized requests.
   Use fixed errors that omit submitted values, tokens and note text.

## 4. Dependencies proposed for approval

| Choice | Reason and cost |
|---|---|
| argparse and tomllib (standard library) | The command tree and static settings fit these modules. No CLI framework, configuration package or additional dev dependency is required. |
| Official Python mcp SDK (runtime) | Owns protocol negotiation, tool discovery, serialization and the two transports. Use the base distribution and review the resolved transitive dependency diff. |
| Uvicorn (runtime, if imported directly) | Approve an explicit pin if the HTTP entry point must serve the SDK's ASGI app with the request limiter wrapped around it. Verify the bounded path with the chosen SDK before pinning. |

At PR 7 select compatible, stable releases aged at least seven days under the existing uv cutoff. Pin direct
dependencies exactly and check uv sync --locked. Present exact versions, release dates and lockfile delta for
owner review before merge. [ADR-0013](../adr/0013-stack-and-dependency-policy.md)

## 5. Acceptance and security checkpoint

- Write new application cases test-first with in-memory fakes. Exercise inbound adapters against the real Phase 2
  container and synthetic fixtures. Run pytest with the core coverage gate, Ruff lint and format, strict mypy and
  import-linter on each PR.
- Test the four tools with public, internal and restricted fixtures. Cross listener and token ceilings in both
  directions. Check overview, search, direct and section reads, hub membership and two-hop relations for hidden
  identifiers and content. Cover missing, malformed, expired and revoked tokens, propose-only scope, spoofed
  headers and revocation between calls. Inspect protocol errors and captured logs for disclosure.
- In WSL dev, issue separate internal tokens for Claude Code and Codex. Register the dev endpoint in both real
  clients, list the four tools and call each against known fixtures. Record client versions, transport, expected
  and observed visible results, and the hidden-id check for each client. Exercise stdio separately in dev.
  Stop the dev server and repeat a call in each client. Record whether it reports “Cérebro offline” and
  continues, then restart and confirm recovery. Update the global instructions to refer only to available tools
  until Phase 4 enables cerebro_propose. Publish credentials and endpoints as placeholders.
- Close only after both client runs and the owner's security checkpoint. Record merged PRs, coverage, review
  decisions and any accepted residual risks. A missing client run remains an open acceptance item.
