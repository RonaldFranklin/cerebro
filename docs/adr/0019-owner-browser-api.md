# ADR-0019: Same-origin owner browser API and local web toolchain

- **Status:** accepted, amended by [ADR-0020](0020-direct-agent-writes.md); container binding amended by [ADR-0027](0027-container-loopback-publication.md)
- **Date:** 2026-11-25

## Context

The owner needs browser access to six views over the existing use cases. MCP already serves agents on
loopback listeners with different sensitivity ceilings. Browsers introduce ambient cookie credentials,
untrusted Markdown and cached views of private content.

## Decision

Serve the built UI, its assets and FastAPI JSON endpoints on the same origin. Bootstrap injects the
accepting socket's identity. Use numeric loopback binds, a private restricted listener and an optional
internal listener disabled by default. Preserve MCP's bearer checks, limits and lifespan. For browser
requests, bind sessions to both the configured external origin and issuing listener. Owner writes, Inbox
and health require the private restricted listener. Lower-ceiling reads filter all references and graph data.

Use a local password prompt and an owner-only, versioned scrypt verifier with a random salt. Reserve a login
attempt in SQLite before hashing. Share the five-attempt rolling fifteen-minute window across processes
and cap concurrent hashes. Issue an opaque random session id and store its digest in bounded process
memory. Rotate on login, revoke on logout or password reset, and expire at 30 idle minutes or eight hours
absolute. Bind each session to the generation of the exact verifier snapshot used for the successful password check. On each authenticated request read
the current generation from the protected verifier, fail closed if unavailable, and reject sessions created
before an external CLI password rotation. Test an external reset during login and after session issuance. Use separate host-only Secure, HttpOnly, SameSite=Strict cookies for the two listeners. Validate
the exact configured Origin and session-bound CSRF token on authenticated mutations. Require same-origin
JSON and a custom header at login. Keep session and API responses out of caches. Browsers send host-only
cookies to every port on that host. The same-host, two-port coexistence test uses a local fixture and trusted
loopback endpoints. Before exposing a lower-ceiling listener through a separate proxy trust boundary, configure
distinct browser hostnames so private credentials never reach the public relay (Phase 8).

Render note text through an HTML-disabled or HTML-escaping Markdown parser followed by allowlist
sanitization. Serve a restrictive CSP with self-hosted assets, a per-response nonce for editor styles and
a same-origin graph worker. The browser UI uses the owner's revision and content-hash checks for writes.
Translate prose through semantic HTML and preserve code, links and identifiers through translation boundaries.

| Delivery PR | Port or dependency responsibility |
|---|---|
| 2 | Pin FastAPI and its required annotated-doc distribution, Node 26.13.1, pnpm 12.22.0 and the approved web packages. The [approved inventory](../plans/archive/phase-5.md#4-dependency-proposal-and-verified-versions) records each package's version and role. |
| 3 | Define application protocols for credential verification, durable login attempt reservation and ephemeral sessions as needed. Provide fakes and contract tests against the protected verifier file, SQLite throttle and bounded memory session store. Reuse the existing clock. |
| 4–6 | Compose the HTTP adapter beside MCP, projecting use cases and DTOs for reading and reviewed writes. Keep storage and application ports behind bootstrap. |
| 7–11 | Build the UI with React, TypeScript, Vite and TanStack Query. Use Marked/DOMPurify for reading, Sigma/Graphology/ForceAtlas2 for the graph, CodeMirror for editing and Playwright for component, browser and screenshot verification. |

## Consequences

Restarting the service invalidates browser sessions and preserves the login throttle. Header values cannot
raise a socket ceiling. The application serves a static web build, so the deployed service needs no Node
runtime. WSL development uses fixture content with HTTPS for browser tests.

## Alternatives considered

A separately hosted frontend would require cross-origin credentials. Browser bearer tokens would expose
a reusable credential to client scripts. Persistent browser sessions would increase the backed-up
credential state for a single-user service.
