# Phase 5 plan — HTTP API and owner web UI

**Approved by the owner on 2026-11-25, including the adjustments in §6.** Deliver the six owner screens on the existing private service. Phase 7 follows this phase. Docker validation in WSL and deployment to the server belong to Phase 6. The Phase 3 manual checks and the Phase 4 security checkpoint retain their recorded status. [Roadmap](../roadmap.md) · [Architecture](../architecture.md) · [Previous delivery](phase-4.md)

## 1. Delivery sequence

PR 1 records the decision in [ADR-0019](../adr/0019-owner-browser-api.md). PR 2 pins the approved inventory. PR 3 implements owner authentication. PR 4 mounts the authenticated browser service. PR 5 implements browser reads. PR 6 implements private owner writes. PR 7 delivers the browser foundation. PR 8 delivers search and the note reader. PRs 9–11 are planned. Start each branch from current main after the preceding PR merges. The owner authorizes merges for all eleven PRs, including security and dependency changes, with independent review, findings resolved, completed green CI and scripts/merge-pr.sh. The dependency, ADR and privileged setup boundaries are recorded in §6.

| PR and branch | Deliverable | Verification before merge |
|---|---|---|
| 1 · `docs/phase-5-start` | Record approval, new dependencies, browser session and listener design in an ADR. Mark the phase in progress. | Check consistency with ADRs 0001, 0005, 0009, 0010, 0011, 0012, 0013 and 0017. Trace each new port and dependency to a later PR. |
| 2 · `build/phase-5-dependencies` | Pin the approved Python and web dependencies, Node patch and pnpm; add web manifest, .nvmrc, both locks and the CI runtime pin. Update docs/architecture.md §7 with the actual resolved versions. | Reproduce locked installs from clean WSL/Linux. Verify peers, release ages for every resolved artifact and browser download, native build and unchanged existing Python pins. Attach the inventory and explain any proposed change before adopting it. |
| 3 · `phase-5/owner-auth` | Owner password provisioning, login throttling, session issuance, expiry and revocation. Inject clock and identity into application use cases. | Test-first with fakes, then real scrypt and shared store contracts. Test bad credentials, parallel attempts, rolling window, revocation after and during a password reset outside the issuing process, logout, process restart and safe state upgrade. |
| 4 · `phase-5/http-shell` | Mount FastAPI and the distribution assets alongside /mcp. Add session endpoints, CSRF and security headers. | Real sockets plus HTTP integration tests for login/logout, limits, CSRF, host and origin checks, cookie flags, cache policy and public/private separation. Test startup refusal for a non-loopback bind and verify the public port is closed by default. All five MCP tools and the CLI keep passing. |
| 5 · `phase-5/read-api` | Typed search, overview, note, graph, neighborhood and health endpoints. Extend the graph projection with area and permitted sensitivity. | Compare API results against use cases and fixtures at each ceiling. Test hidden/missing equality, invisible edge endpoints, history, hub membership, filtered results and restricted-only health. |
| 6 · `phase-5/owner-write-api` | Owner edit and Inbox list, preview/diff, amend, approve/apply, resume and reject. | Exercise preview → concurrent edit → conflict with zero overwrite, restricted-history warning, validation errors, and one git commit with trailers after successful apply. |
| 7 · `phase-5/web-foundation` | Build the dark application shell, login, session handling, typed API client and test harness. Serve the production build through the backend. | Frozen install, strict TypeScript, production build, keyboard login/logout, expiry and cache clearing. Verify SPA deep links and static asset allowlist on the backend origin. |
| 8 · `phase-5/search-reader` | Search and note reader, section navigation, sanitized Markdown and browser translation boundaries. | Browser component tests cover empty results, filtering, hub/section navigation, text semantics, blocked unsafe markup/URLs and translation attributes. |
| 9 · `phase-5/graph` | Sigma.js global/local graph, ForceAtlas2 worker, filters, legend and keyboard-accessible node list. | Test color and size mapping, topology after filters, click → local neighbors → back. Exercise the production worker under CSP, plus fallback navigation with WebGL unavailable. |
| 10 · `phase-5/inbox-editor` | Inbox diff and review actions, CodeMirror owner editor, visible wikilink completion, preview and conflict recovery. | Component tests cover create/update diffs, validation, accept/reject, safe resume and stale revisions. Verify a pending edit survives a conflict and can be inspected before retry. |
| 11 · `phase-5/health-acceptance` | Health panel, installed-service browser acceptance, WSL runbook and phase report. | Run all backend and frontend gates. Inspect the fixture git commit, check CSP and invisible content, record coverage and decisions, update roadmap and AGENTS.md, then hand the security checkpoint to the owner. |

## 2. Browser API and security decisions

- **One origin.** Bootstrap owns the sockets and injects use cases and an immutable listener identity. Keep the private port at `restricted`, the optional public port at `internal`, both on 127.0.0.1 by default. Reject non-loopback bind addresses for either listener at startup. Keep the public listener disabled unless explicitly enabled for testing. Serve built assets at /assets/, the application at /, JSON at /api/ and the existing bearer protocol at /mcp. Mount MCP with its existing lifespan, bearer checks and input limits. Apply cookie/CSRF middleware only to the browser endpoints. SPA fallback applies only to known browser navigation routes. Missing assets, API paths and MCP requests retain their own status codes. Serve content-addressed public assets from the build directory only, with immutable caching. Keep private data out of the bundle. Use no cross-origin credential sharing or remotely hosted assets.
- **Authorization.** Use an owner cookie for the browser API. MCP continues to require bearer tokens and the minimum of the verified token and accepting socket ceilings. A browser session is bound to its issuing listener and configured external origin. Construct an owner context for that socket on each request. Reject session reuse on another listener, including when cookies are sent across ports on the same host. Forwarded headers, Host and request fields never choose the ceiling. At the API boundary allow only configured hosts/origins. Keep Inbox, owner writes and health on the restricted private listener. Lower-ceiling reads filter notes, links, graph nodes, counts and filter options server-side. Hidden and absent ids produce identical public responses.
- **Login.** Provision one versioned, salted verifier outside both repositories, using a local interactive password prompt. Use stdlib hashlib.scrypt with a random salt of at least 16 bytes, N=2^17, r=8, p=1, an explicit sufficient maxmem and constant-time digest comparison. Cap password bytes before hashing and reject unsupported verifier parameters. Protect the verifier with owner-only permissions. Atomically reserve one of five attempts per rolling 15 minutes per instance before expensive verification and limit concurrent hash computations. Store the bounded attempt window in state.sqlite so restarts and concurrent listeners share the limit. Use generic failures and a bounded retry delay. Verify real timings/memory on WSL before close. [Password storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#scrypt)
- **Session and CSRF.** Recommend an opaque random 256-bit session id, stored as a digest server-side in bounded process memory for the existing single service process. Expire after 30 idle minutes or 8 hours absolute. Rotate on login, revoke on logout or credential reset, and invalidate on restart. Set distinct host-only cookies (`__Host-cerebro_private` and `__Host-cerebro_public`). Each listener accepts only its own cookie name and validates the stored binding. Set each cookie with `Secure; HttpOnly; SameSite=Strict; Path=/`. Require HTTPS at the browser. Validate the configured exact Origin and a session-bound synchronizer token in a custom header for every authenticated state change, including logout. Supply the CSRF token in a same-origin no-store session response and compare it in constant time. For login require the configured Origin, JSON content type and a non-simple custom request header. All mutations use unsafe HTTP methods; GET/HEAD are side-effect free. Cap request streams and fields before use-case dispatch. Keep sensitive values out of logs, URLs, browser persistence and caches. Send `Cache-Control: no-store` on session/API responses. On logout/expiry purge the query cache, pending requests, graph and editor state.
- **Content rendering.** Disable or escape raw HTML at the Markdown parser. Sanitize the final fragment with a narrow HTML/attribute allowlist and a URL policy before insertion. Allow ordinary formatting, headings, lists, tables, links and code. Block event handlers, style, embedded documents, forms, SVG, remote images and unsafe URL schemes. Generate internal wikilinks only from validated ids in visible DTOs. Use escaped text for diff and health fields. Route reader, editor preview and Inbox preview through the same renderer. Test DOM semantics and the absence of executable/network-capable nodes with inert fixtures.
- **CSP.** Enforce the policy on the built application from the first browser PR. Start from `default-src 'none'; script-src 'self'; style-src 'self' 'nonce-<per-response>'; style-src-attr 'none'; connect-src 'self'; img-src 'self'; font-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'`. Generate a fresh nonce for each HTML response, never cache the nonce-bearing shell, and pass it to CodeMirror's style-module nonce option. Ship CSS and the layout worker as same-origin files; call the synchronous ForceAtlas2 API *inside* that worker. Verify editor geometry, graph canvases and interaction in Chromium against the production policy. Resolve any style incompatibility in that PR before accepting the component. Set nosniff and a no-referrer policy. [ForceAtlas2 worker supervisor](https://github.com/graphology/graphology/blob/master/src/layout-forceatlas2/worker.js)
- **Application boundary.** FastAPI handlers translate bounded input into DTOs. Keep ports, storage and git behind the composition root. Return structured errors (401 expired, 403 forbidden, 404 invisible/absent, 409 stale, 422 validation, 429 throttled). Add allowlisted fields to GraphView and its index projection where needed. Preserve MCP output contracts. Thread area through NoteRef and the rebuildable SQLite graph schema, with upgrade/rebuild coverage. Extend the private Inbox preview with approved-to-display validation, original/base state and candidate from the same inspected revision, including an explicit stale-base indication. Compare the stored proposal base hash to the live note. Label the left side as current when the original revision is unavailable, and block stale approval. For a create, compare against an empty base. Use stdlib difflib for bounded text/field differences. Keep raw rejected proposal payloads unavailable. Submit owner changes through OwnerEditNote with the inspected base hash. Inbox mutations use the inspected proposal revision; approval calls ReviewProposal followed by serialized ApplyProposal and supports safe resume. Test actual trailers and single-commit behavior.

## 3. Screen acceptance

Use a quiet dark palette, high-contrast text, visible keyboard focus and a consistent navigation rail that collapses on narrow screens. Show loading, empty, unavailable, denied and conflict states where the owner can act on them. Use text/shape alongside color. Keep all interface copy in English.

| Screen | Owner workflow |
|---|---|
| Graph | Color nodes by area by default, with an optional type toggle. Scale size monotonically P0 > P1 > P2 > P3. Filter by area, type, priority, status, sensitivity and history. Show only edges connecting visible nodes. Click or keyboard-select a node for a local view (one hop, optionally two), open the reader, or return to the previous global filters. Provide an equivalent HTML list for keyboard use, translation and WebGL fallback. |
| Reader | Render paragraphs and headings as real HTML with the note's lang. Set translate="no" on code blocks, inline code, ids, paths, commands, links and wikilinks. Mark command/path text through explicit code semantics and recognized inline tokens. Keep surrounding prose translatable. Render visible hub members separately from the introduction. |
| Search | Search summaries, filter by metadata, opt into historical notes, and navigate to the matched note or section. Cancel superseded searches so stale results cannot replace the current query. |
| Inbox | Inspect proposer, validation and NullCurator decision. Show changes to frontmatter and body with a before/after diff; mark new notes and stale bases. Review, amend, reject with reason, or approve the revision on screen. Show the resulting commit or conflict, and surface restricted-history warnings. |
| Owner editor | Edit Markdown in CodeMirror 6 with structured metadata controls, visible-id wikilink autocomplete, translated-prose preview and explicit save. Show validation next to the field, preserve the draft on conflict, and reload/compare before retry. |
| Health | Show index freshness, broken links, orphaned notes, budget and scanner findings from HealthReport, with severity and safe links. Show fixed scanner codes or line numbers without matching text. Keep the panel read-only. |

## 4. Dependency proposal and verified versions

Snapshot for approval on 2026-11-25. The cut-off used for the published artifact inventory was
2026-11-18 23:59 UTC. Dates below are UTC publication dates. Keep exact manifest pins, commit both lockfiles
and recheck age, availability and peer compatibility at the dependency PR. The Python additions resolved
against a temporary copy of the current backend lock on Python 3.14. The npm candidates resolved together
with strict peers, passed an isolated frozen install with lifecycle scripts disabled and loaded the Vite and TypeScript CLIs on WSL.
These checks exercise dependency resolution. Application and browser acceptance belong to the delivery PRs.

| Package | Exact version | Published | Purpose | Lighter option or constraint |
|---|---:|---|---|---|
| [FastAPI](https://pypi.org/project/fastapi/0.165.0/) | 0.165.0 | 2026-11-18 | Bare ASGI router, typed request handling and integration with existing Uvicorn. | Direct Starlette routing with explicit validation. |
| [annotated-doc](https://pypi.org/project/annotated-doc/0.0.7/) | 0.0.7 | 2026-11-18 | Required by FastAPI and the only new transitive backend distribution in the trial lock. | Required with this FastAPI release. |
| [pnpm](https://registry.npmjs.org/pnpm) | 12.22.0 | 2026-11-17 | Package manager. Frozen lock, strict peers and minimum release age. | Bundled npm with a separate lock-age check. |
| [react](https://registry.npmjs.org/react) | 19.3.1 | 2026-10-14 | Runtime. Components and state shared across the six views. | Native DOM and events require manual component lifecycle. |
| [react-dom](https://registry.npmjs.org/react-dom) | 19.3.1 | 2026-10-14 | Runtime. Render React components into accessible HTML. | Native DOM if React is removed. |
| [@tanstack/react-query](https://registry.npmjs.org/@tanstack/react-query) | 5.108.0 | 2026-11-17 | Runtime. Cancel requests, cache responses and invalidate data after review or edits. Clear on sign-out. | Typed fetch wrapper with manual caching. |
| [sigma](https://registry.npmjs.org/sigma) | 4.0.6 | 2026-11-10 | Runtime. WebGL graph and pointer picking on large graphs. | Canvas with custom hit testing and camera. |
| [graphology](https://registry.npmjs.org/graphology) | 0.26.0 | 2025-01-26 | Runtime. Graph model used by Sigma and the layout. | Adjacency maps would need a Sigma adapter. |
| [graphology-layout-forceatlas2](https://registry.npmjs.org/graphology-layout-forceatlas2) | 0.10.1 | 2022-10-17 | Runtime. Run layout off the UI thread with a self-hosted module worker. | Deterministic circular placement loses clustering. |
| [@codemirror/state](https://registry.npmjs.org/@codemirror/state) | 6.7.6 | 2026-09-22 | Runtime. Edit transactions and selection state. | Native textarea with browser selection. |
| [@codemirror/view](https://registry.npmjs.org/@codemirror/view) | 6.43.15 | 2026-10-19 | Runtime. Interactive editor, keyboard focus and selection. | Native textarea. |
| [@codemirror/commands](https://registry.npmjs.org/@codemirror/commands) | 6.11.1 | 2026-09-15 | Runtime. Keyboard editing, undo and redo. | Native textarea history. |
| [@codemirror/lang-markdown](https://registry.npmjs.org/@codemirror/lang-markdown) | 6.5.2 | 2026-08-04 | Runtime. Markdown syntax support. | Plain-text mode. |
| [@codemirror/autocomplete](https://registry.npmjs.org/@codemirror/autocomplete) | 6.20.4 | 2026-10-19 | Runtime. Complete visible wikilink targets at the cursor. | Separate manual target picker. |
| [marked](https://registry.npmjs.org/marked) | 18.2.1 | 2026-11-16 | Runtime. Parse Markdown and override raw HTML tokens at the rendering boundary. | Restricted plaintext plus links loses Markdown structure. |
| [dompurify](https://registry.npmjs.org/dompurify) | 3.4.18 | 2026-11-14 | Runtime. Sanitize final rendered markup to an explicit HTML/attribute/protocol allowlist. | Render a restricted token tree solely with safe DOM operations. |
| [typescript](https://registry.npmjs.org/typescript) | 7.0.2 | 2026-07-08 | Build. Strict checking of API DTOs, routes and React props. | JavaScript plus JSDoc provides less coverage. |
| [vite](https://registry.npmjs.org/vite) | 8.3.8 | 2026-11-11 | Build. Generate static JS, CSS and worker assets with hashed filenames and TSX support. | Single bundler plus manual asset pipeline. |
| [@types/react](https://registry.npmjs.org/@types/react) | 19.3.0 | 2026-09-09 | Build. React props, hooks and JSX types for strict TypeScript. | Required by the selected React/TS combination. |
| [@types/react-dom](https://registry.npmjs.org/@types/react-dom) | 19.3.0 | 2026-09-09 | Build. Type the browser root and mount API. | Required for a typed React DOM entry point. |
| [@types/node](https://registry.npmjs.org/@types/node) | 26.6.6 | 2026-10-28 | Build. Node 26 declarations for tooling and test configuration. | Use JavaScript-only config files and omit if CI type checks pass. |
| [graphology-types](https://registry.npmjs.org/graphology-types) | 0.24.8 | 2024-11-22 | Build. Resolve the shared Graphology and ForceAtlas2 type peer explicitly. | Use a different graph model and renderer. |
| [@playwright/test](https://registry.npmjs.org/@playwright/test) | 1.65.1 | 2026-11-13 | Test. Chromium browser assertions for isolated React components and the full application. | Node test runner handles pure functions only. |

Uvicorn, Pydantic and Starlette are already in the project or its lock. Password hashing, random session
secrets, constant-time checks, diff generation and SQLite storage use the Python standard library. Vite
handles TSX using the automatic React JSX transform. A browser-only component fixture mounts real React
components with deterministic inputs and request stubs under Playwright. Exclude the fixture and stubbed
API from the production build. The full E2E test launches the installed backend serving the production
bundle and uses a separate temporary instance.

Set `minimumReleaseAge: 10080` and `strictPeerDependencies: true` in `pnpm-workspace.yaml`. Set
`ignoredOptionalDependencies: ['@rolldown/binding-wasm32-wasi']` there too. That optional compiler
target pulls prerelease WASM support packages. The native WSL/Linux compiler passed a frozen install.
Use a bundled module worker that runs the synchronous ForceAtlas2 algorithm. The package's own worker
supervisor constructs a blob URL, which conflicts with a same-origin worker policy. CodeMirror can receive
a per-response style nonce through `EditorView.cspNonce`. Browser tests must run with the production CSP.
[ForceAtlas2 sources](https://github.com/graphology/graphology/tree/master/src/layout-forceatlas2) ·
[CodeMirror view reference](https://codemirror.net/docs/ref/#view.EditorView%5EcspNonce) ·
[pnpm settings](https://pnpm.io/settings)

<details>
<summary>Additional npm artifact pins in the planning resolution</summary>

The committed lock contains 121 distinct package versions across both pnpm lockfile documents, including
optional native binaries for other platforms. The table above gives the direct choices. The remaining
families below are grouped where their pinned version and publication date match. PR owners will attach
the actual resolved lock diff and report any change from this snapshot. The browser revision packaged by
`playwright-core@1.65.1` is Chromium/headless shell 1255 (159.0.8154.6), with FFmpeg revision 1013.
The publisher CDN dates the Linux x64 Chromium and headless shell artifacts to 2026-11-11 and FFmpeg to 2026-09-30 (Last-Modified). In PR 2 verify the selected distribution URLs, platform and publication/availability dates against the seven-day cutoff for each binary before installation. Record the evidence for the CI and WSL architecture. Use the existing pinned Actions and the verified Linux Node archive for CI. [Chromium archive](https://cdn.playwright.dev/builds/cft/159.0.8154.6/linux64/chrome-linux64.zip) · [Headless shell](https://cdn.playwright.dev/builds/cft/159.0.8154.6/linux64/chrome-headless-shell-linux64.zip) · [FFmpeg archive](https://cdn.playwright.dev/dbazure/download/playwright/builds/ffmpeg/1013/ffmpeg-linux.zip) · [Playwright browser manifest](https://github.com/microsoft/playwright/blob/v1.65.1/packages/playwright-core/browsers.json)

| Additional artifact(s) | Exact version | Published |
|---|---:|---|
| `@pnpm/exe.*` (15 native targets) | 12.22.0 | 2026-11-17 |
| `@rolldown/binding-*` (15 native targets) | 1.2.19 | 2026-11-11 |
| `@typescript/typescript-*` (20 native targets) | 7.0.2 | 2026-07-08 |
| `lightningcss-*` (11 native targets) | 1.33.0 | 2026-07-20 |
| `@codemirror/lang-css` | 6.3.1 | 2024-11-26 |
| `@codemirror/lang-html` | 6.4.12 | 2026-08-04 |
| `@codemirror/lang-javascript` | 6.2.5 | 2026-03-02 |
| `@codemirror/language` | 6.13.1 | 2026-10-07 |
| `@codemirror/lint` | 6.9.8 | 2026-11-13 |
| `@codemirror/streamparser` | 6.0.0 | 2026-10-07 |
| `@lezer/common` | 1.5.5 | 2026-10-30 |
| `@lezer/css` | 1.3.9 | 2026-10-07 |
| `@lezer/highlight` | 1.2.5 | 2026-09-28 |
| `@lezer/html` | 1.3.13 | 2025-12-22 |
| `@lezer/javascript` | 1.5.6 | 2026-10-01 |
| `@lezer/lr` | 1.4.12 | 2026-10-30 |
| `@lezer/markdown` | 1.8.1 | 2026-10-19 |
| `@marijn/find-cluster-break` | 1.0.4 | 2026-08-21 |
| `@oxc-project/types` | 0.156.0 | 2026-10-27 |
| `@rolldown/pluginutils` | 1.1.1 | 2026-11-13 |
| `@tanstack/query-core` | 5.108.0 | 2026-11-17 |
| `@types/trusted-types` | 2.0.7 | 2023-11-21 |
| `crelt` | 1.0.7 | 2026-06-27 |
| `csstype` | 3.2.4 | 2026-11-17 |
| `detect-libc` | 2.1.2 | 2025-10-05 |
| `events` | 3.3.0 | 2021-02-27 |
| `fdir` | 6.5.0 | 2025-08-14 |
| `fsevents` | 2.3.3 | 2023-08-21 |
| `graphology-utils` | 2.5.2 | 2022-04-07 |
| `lightningcss` | 1.33.0 | 2026-07-20 |
| `nanoid` | 3.3.20 | 2026-10-05 |
| `picocolors` | 1.1.1 | 2024-10-16 |
| `picomatch` | 4.0.7 | 2026-08-24 |
| `playwright`, `playwright-core` | 1.65.1 | 2026-11-13 |
| `postcss` | 8.5.33 | 2026-11-13 |
| `rolldown` | 1.2.19 | 2026-11-11 |
| `scheduler` | 0.28.0 | 2026-09-09 |
| `source-map-js` | 1.2.3 | 2026-11-18 |
| `style-mod` | 4.1.5 | 2026-11-17 |
| `tinyglobby` | 0.2.17 | 2026-05-30 |
| `undici-types` | 8.9.0 | 2026-07-24 |
| `w3c-keyname` | 2.2.8 | 2023-06-07 |

</details>

## 5. WSL development and verification

**Node selection.** Use **26.13.1 (Lithium)** on WSL Linux. The official release index dates this artifact to November 10, 2026, and the release announcement is dated November 11. Both satisfy the seven-day minimum on November 25. Pin the exact patch in .nvmrc, package.json engines and CI. Pin pnpm in packageManager. A WSL installation with nvm already available can use `nvm install 26.13.1 && nvm use 26.13.1`. [Node release schedule](https://github.com/nodejs/Release/blob/main/schedule.json) · [Node release index](https://nodejs.org/dist/index.json) · [Node release announcement](https://nodejs.org/en/blog/release/v26.13.1)

For a fresh WSL Ubuntu shell with curl, tar, xz and sha256sum, install the official Linux binary under the current user's account. These checksums are for the exact release. Run the commands from Bash. [Publisher checksums](https://nodejs.org/dist/v26.13.1/SHASUMS256.txt)

```bash
(
  set -eu
  case "$(uname -m)" in
    x86_64) arch=x64; digest=5c063e46bab0fbc1c17cc445d7ff129cbdcd0471be6ed83b6ab864f8143bc864 ;;
    aarch64) arch=arm64; digest=89e1853138db51e8b82d3e98ba9c0d3f2956a28e5e55ea1b7e12d25aae92d5dd ;;
    *) echo 'Select a supported Linux archive from the Node release index.' >&2; exit 1 ;;
  esac
  tmp="$(mktemp -d)"
  trap 'rm -rf -- "$tmp"' EXIT
  archive="node-v26.13.1-linux-$arch.tar.xz"
  cd "$tmp"
  curl -fsSLO "https://nodejs.org/dist/v26.13.1/$archive"
  printf '%s  %s\n' "$digest" "$archive" | sha256sum -c -
  mkdir -p "$HOME/.local/opt"
  tar -xJf "$archive" -C "$HOME/.local/opt"
)
```

Stop if verification fails. Select the installed Node in each new shell, or use the nvm selection above. Install the native pnpm binary for glibc-based WSL Ubuntu. Verify the archive against the SHA-512 digest from the committed lock before extracting it.

```bash
case "$(uname -m)" in x86_64) arch=x64 ;; aarch64) arch=arm64 ;; esac
export PATH="$HOME/.local/opt/node-v26.13.1-linux-$arch/bin:$PATH"   # archive installation
node --version                                                    # v26.13.1
pnpm_dir="$HOME/.local/opt/cerebro-pnpm-12.22.0"
(
  set -eu
  case "$arch" in
    x64) digest=881019018098a28c3a83468e6ca9a9aca9080463bbda62b82857ea22b63b8c874746401b37aeed70dd3381a3c4dfd50011d31cae42e12f012697e54cc5ff21f1 ;;
    arm64) digest=d4c67719f14215c92ab8024fe9667df392eb77930fe58340eef3d91fbf56fb5415cf0979e5f54c76c00e7337469c5794e23a8cc04467935dbdff583dcd8c610f ;;
    *) exit 1 ;;
  esac
  tmp="$(mktemp -d)"
  trap 'rm -rf -- "$tmp"' EXIT
  cd "$tmp"
  curl -fsSLo pnpm.tgz "https://registry.npmjs.org/@pnpm/exe.linux-$arch/-/exe.linux-$arch-12.22.0.tgz"
  printf '%s  pnpm.tgz\n' "$digest" | sha512sum -c -
  mkdir -p "$pnpm_dir"
  tar -xzf pnpm.tgz --strip-components=1 -C "$pnpm_dir" package/pnpm
)
if (( $? == 0 )); then
  export PATH="$pnpm_dir:$PATH"
  pnpm --version                                                  # 12.22.0
else
  echo 'pnpm installation failed.' >&2
  false
fi
```

**Commands the implementation PRs must provide.** From the repository root on x86-64 WSL Ubuntu, run the following against a disposable synthetic instance. The browser binary inventory covers Linux x64; the ARM64 instructions above cover the Node and pnpm installations. The web commands and fixture launcher are planned deliverables. Keep the existing CLI and MCP entry points usable without installing Node on the service host.

```bash
(cd backend && uv sync --locked && uv run --locked ruff check . && uv run --locked ruff format --check . &&
  uv run --locked mypy && uv run --locked lint-imports --no-cache && uv run --locked pytest --cov)
(cd web && pnpm install --frozen-lockfile && pnpm run typecheck && pnpm run build &&
  pnpm exec playwright install chromium && pnpm run test:component && pnpm run test:e2e)
(cd backend && uv run --locked python -m devtools.web_demo --dist ../web/dist)
```

The demo launcher creates a temporary git/SQLite/Fernet fixture with public, internal and restricted notes, a reviewable proposal and a health finding. It prompts locally for the owner password and serves the built app and API together over dev-only loopback HTTPS, with a disposable localhost certificate and a printed fingerprint. Open the displayed URL from the WSL host browser, verify the development certificate, and sign in. PR 11 documents setup, expected observations, shutdown and cleanup. Use local fixture trust in the isolated browser test context. Docker, hml credentials and server access are unnecessary for this phase.

**Coverage of the acceptance run**

- Backend gates match the existing CI, including the 90% domain/application coverage threshold, ResourceWarning enforcement, strict mypy, Ruff and import contracts. Add contracts for any new port with fake and SQLite implementations. Verify both sockets, simultaneous sessions on two ports of the same hostname in one browser profile, session replay across ports, CSRF on login and authenticated mutations, password rotation, a throttled verifier, hidden/missing results, redacted logging, stale edits and git trailers.
- Mount real React components with fixture API responses in a browser harness. Exercise graph keyboard navigation and filters, sanitized reader and translation markup, empty/error search, Inbox diff, editor conflict and health statuses. Keep the harness and its fixtures out of the release bundle.
- Run at least one E2E in Chromium against the built distribution and real backend. Log in, find a synthetic note, open its graph neighborhood and translated-prose reader, review a diff, approve the inspected revision, save an owner edit, verify the resulting git history and sign out. Confirm reloading a protected route requests login. Run a negative session on the lower-ceiling test socket and assert restricted identifiers never occur in API responses or the DOM. Also exercise the ADR-0010 boundary with a test-only TLS terminator forwarding to the plain loopback HTTP socket. Verify login, a CSRF-protected mutation and logout against the configured external HTTPS origin, then verify forwarding headers cannot elevate the public listener. This fixture needs neither Tailscale nor Docker. Capture CSP violations for the editor, Markdown preview and graph worker. Disable traces containing cookies, passwords or note bodies.
- Record packages and artifact ages, CI results, coverage, observed accessibility/CSP behavior, unresolved decisions and the owner security checkpoint in the final report.

## 6. Owner approval and adjustments

The owner approved PR #55, all dependencies in §4, user-space Node 26.13.1 and pnpm 12.22.0. Run the eleven approved steps autonomously. Stop for the one-time WSL provisioning below, a dependency outside the approved list, a required deviation from an ADR, or a blocker.

| Applies to | Required adjustment |
|---|---|
| Start of PR 2 | Inventory all privileged WSL setup for the entire phase, including Chromium system libraries. Check installed packages and the pinned browser's requirements without elevation. Give the owner the exact commands in a single hand-off and wait for the owner to run them. Use user-space installs and ordinary unprivileged test commands thereafter. |
| PR 2 onward | Try TypeScript 7 against Vite, Playwright and CodeMirror. If a verified incompatibility requires a fallback, select the newest stable preceding compatible TypeScript release at least seven days old and record the failing scenario, version, release date, changed lock and passing reproduction. The owner has authorized this fallback. |
| Each of PRs 7–11 | Capture the screens delivered by that PR with Playwright against synthetic fixtures and commit the images in `docs/screenshots/phase-5/`. Link directly to those images in that PR's description. The owner's visual review follows delivery and is independent of the CI and merge gates. |

At close, document the exact WSL command to open the UI through `devtools.web_demo`, alongside the usual delivery, tests, decisions and security report.

### PR 2 delivery record

The owner completed the single privileged WSL hand-off. The pinned Playwright CLI reported that
the Chromium system packages were installed. Node and pnpm were installed under the user account.
The Linux x64 browser downloads matched the three URLs and dates in §4. The committed web lock
matches the approved inventory, and the Python lock adds only FastAPI and annotated-doc.
TypeScript 7 checks Vite, Playwright and CodeMirror types in strict mode. The native Vite build
bundles a Markdown editor. Playwright opens it over fixture HTTPS and verifies editor styling under the browser CSP. On x86-64 WSL Ubuntu, run this compatibility check with
`cd web && pnpm install --frozen-lockfile && pnpm exec playwright install chromium && pnpm run test:toolchain`.

### PR 3 delivery record

Run `cerebro --config /path/to/instance.toml owner-password` in a local terminal to provision or rotate
the browser password. Its UTF-8 encoding must fit in 1–1024 bytes. Keep the configured state directory
outside both repositories. The CLI checks the configured content path, Git worktree markers and metadata,
the executing source checkout and the standard application source layout. With a separately installed wheel,
a sparse explicit worktree can omit both its Git marker and the recognizable source files. For that layout the
operator must export `GIT_WORK_TREE` with the application's checkout root when invoking the owner CLI or
browser service. Git's standard variable adds that tree to the excluded paths. Select an external state
directory for verifier storage. The CLI atomically replaces its protected verifier and uses a persistent
sibling lock to limit the instance to one simultaneous scrypt operation. On WSL2 a real login took
0.203 seconds, with 155 MiB peak process RSS. Every attempted login reserves a slot before verification.
The five-attempt window survives restart and expires individual attempts after fifteen minutes.

The browser service shares a process-local table across listeners. It keeps up to 64 sessions, evicting
the least recently used at capacity. An authenticated request checks the exact verifier generation,
listener, origin and expiry. Restart, logout and external password rotation invalidate prior sessions.

### PR 4 delivery record

The installed `cerebro-mcp` serves the browser API and MCP on the same numeric loopback sockets. Set
`--private-origin` to the canonical external HTTPS origin served by the TLS terminator. Preserve its
Host header on the loopback hop. With `--enable-public`, set `--public-origin` to the second origin.
Defaults are HTTPS on localhost at the respective configured ports. Proxy headers cannot select the
listener or ceiling. Pass `--web-dist` to serve a local production build; the API and MCP work without it.

POST `/api/login` with a JSON password, the exact Origin and `X-Cerebro-Request: 1`. Login and
GET `/api/session` return the listener ceiling and session-bound `csrf_token`. Send `X-CSRF-Token`
and the exact Origin on mutations, including POST `/api/logout`. Each origin receives its own
host-only Secure, HttpOnly, SameSite=Strict cookie. Logout returns 204. API and shell responses
use no-store. The HTML carries the CSP nonce in a `cerebro-style-nonce` meta element.

Integration tests use two test-only TLS terminators forwarding to real private and public loopback
sockets. They exercise the same-host cookies, socket-bound authorization, logout, host/origin checks
and the nonce-bearing shell. Existing installed MCP tests cover both credential scopes, the disabled
public listener, bounded requests and the SDK lifespan.

### PR 5 delivery record

All six GET endpoints require the browser session. Query fields are optional, single-valued and
case-sensitive. GET bodies are rejected at the first nonempty frame. Unknown and repeated fields produce 422. The default excludes history. Set
`history=true` to include it. The 8 KiB encoded query ceiling is checked before parameter parsing.

| Endpoint | Query | Result |
|---|---|---|
| `/api/overview` | None | Visible core notes, word count, truncation flag, areas and hubs. |
| `/api/search` | `q` (up to 1,024 UTF-8 bytes), `area`, `type`, `priority`, `status`, `history`, `limit` (1–100, default 20) | `results` in relevance order. Each summary carries id, title, type, area, priority, status and summary. |
| `/api/notes/{id}` | `section` (up to 256 UTF-8 bytes), `history` | Authorized Markdown, metadata and visible hub members. A full private read exposes the edit precondition `content_hash` only when the visible content matches the stored note. |
| `/api/graph` | `area`, `type`, `priority`, `status`, `sensitivity`, `history` | `nodes` with summary, area and sensitivity, `edges` with source, target, kind and section, and a null `center`. |
| `/api/notes/{id}/neighbors` | Graph filters plus `hops` (1–2, default 1) | The same shape, restricted to connected nodes reachable through matching nodes. Filtering out the center returns an empty graph. |
| `/api/health` | None | Restricted listener only. Checked counts, index status and issues with subject, code, severity, field and safe message. |

Graph options can be derived from the caller's unfiltered graph. Unknown and invisible notes both
return the same 404. Health returns 403 on the public listener. The SQLite graph projection advances
to version 3 and rebuilds from the content repository on startup. The state database and MCP read
response fields retain their existing formats.

### PR 6 delivery record

Owner writes require the restricted listener, a valid session, exact Origin, JSON and the session CSRF
header. PUT `/api/notes/{id}` takes `expected_hash` from the complete private read and a `note` object
containing the editable metadata and Markdown. The path supplies the immutable ID. A successful edit
returns the new content hash, commit, validation and warnings. A stale edit returns 409 without changing
the note. Validation failures return 422. The editor can retain the draft and reload the current revision.

GET `/api/inbox` returns proposal metadata in `items`. GET `/api/inbox/{id}` adds a reviewable candidate,
its base, the curator decision, current validation and historical validation, plus `diff.fields` and a unified Markdown `diff.body`.
The diff compares at most 512 lines and 32,768 characters on each side, caps the returned text at
32,768 characters and marks clipping through `diff.truncated`. The full base and candidate remain available.
A create has an empty base. When the stored hash differs from the live target, the preview labels that
target as current and sets `stale_base`. The preview reports validation as indeterminate when that base
prevents assessment. A missing target carries structural errors. Candidate note dates use the durable
submission date so reopening or resuming on a later day preserves the displayed change. Rejected proposal
bodies are unavailable.

POST to `/api/inbox/{id}/amend`, `/approve`, `/resume` or `/reject` with the inspected `revision`.
Amend also takes a replacement Markdown `body`. Reject requires a `reason`. Approval rechecks the
proposal and its target, then applies the approved revision under the writer lock. If interrupted, reload
the listing and resume the approved revision. Applying an already-applied current revision returns
`already_applied`. Successful changes report the commit and any restricted-history warning.

### PR 7 delivery record

The production Vite build serves the sign-in page and workspace overview from the backend origin.
The overview lists the session's visible core notes, hubs and areas. The navigation shows Inbox and
Health on the private listener. An authenticated deep link retains its path during sign-in. Later
screen PRs replace the unavailable views at their existing paths.

The browser API client sends same-origin requests, uses the session's CSRF token for mutations and
keeps credentials in memory. Each session owns a cancellable request lifetime and its own TanStack
Query cache. Sign-out removes workspace data immediately. If the server cannot confirm revocation,
the screen offers an authenticated retry. A 401 clears the current workspace, as do the local
30-minute idle and eight-hour absolute timers. Returning to the page rechecks server authorization;
a restored browser history snapshot starts with a fresh session check.

Run `pnpm run typecheck`, `pnpm run build`, `pnpm run test:component` and `pnpm run test:e2e` in
`web/` after the locked backend and frontend installs in §5. The component fixture mounts the real
application in isolation with intercepted requests. E2E uses the installed backend and a disposable
HTTPS instance with synthetic notes. To open this build manually, run
`uv run --locked python -m devtools.web_demo --dist ../web/dist` from `backend/`. Choose a disposable
password at the terminal prompt and open the printed private HTTPS origin. Inspect the local
certificate's SHA-256 fingerprint against the terminal output before accepting it. Ctrl+C removes
the instance. The owner installation and global browser trust configuration are untouched.

[Sign-in](../screenshots/phase-5/07-login.png) · [Overview](../screenshots/phase-5/07-overview.png)

### PR 8 delivery record

Search applies the text query, area, type, priority, status and history filters on submit. Filter options
come from the visible graph. A session-scoped query client cancels superseded requests and restores the
submitted criteria on return from the reader. A result opens its note or one of its sections. The reader
preserves the history selection in note, hub and section links. Hub membership is displayed separately
from the note introduction.

The shared Markdown component escapes raw HTML in Marked and allows only approved formatting and link
elements through DOMPurify. Images render as text. Wikilinks open the same-origin reader only when the destination ID belongs to the visible graph for the current history selection.
External HTTP(S) links use a no-referrer policy and exclude the application hostname on every port. The note language applies to prose. Code, links and
recognized inline commands, paths and identifiers preserve their original text during browser translation.
The reader and search screens use the same-origin API, session and CSP.

Playwright exercises filtering, cancellation, missing notes, keyboard navigation, hubs, section links,
HTML sanitization and translation boundaries. The installed-service test captures the production build
against the disposable HTTPS fixture. [Search screenshot](../screenshots/phase-5/08-search.png) ·
[Reader screenshot](../screenshots/phase-5/08-reader.png)
