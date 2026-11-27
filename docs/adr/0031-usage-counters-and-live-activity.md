# ADR-0031: Token usage counters and the owner's live activity channel

- **Status:** proposed
- **Date:** 2026-11-27

## Context

Health is to show each agent token's last use and activity over 24 hours (Phase 8, part C), and the graph is to pulse,
with an activity feed, when an agent reads or writes a note (step 4). Both observe the same MCP traffic. The owner
asked for one design before either is built: events carry no note content, nothing above the session's ceiling is
revealed, connections are bounded, tokens are never logged, and token issuing and validation do not change.

## Decision

### Observation

The composition root adds both observers around what the **MCP adapters** call. `AuthenticateToken`, token issuing and
validation are unchanged, and the owner's browser and CLI raise nothing. An observer acts only after success, never
changes a result or an error, and drops its own failures.

| | Usage counter | Activity event |
|---|---|---|
| Raised when | an HTTP request on `/mcp` has authenticated | a note is read by `cerebro_read`, or created or updated by `cerebro_write` |
| Counted or sent | token id and time | note id, verb (`read` or `write`), agent name, time |
| Kept in memory only, never sent | | the note's sensitivity at that moment |
| Not raised by | refused credentials, including a token without the needed scope (403) | overview, search, neighbors, refused writes, replayed creates |

- **Counting point.** The MCP adapter authenticates more than once while serving one request. The observer is called
  once per HTTP request, after the first authentication is accepted, not by wrapping `AuthenticateToken`.
- **What a write must tell.** `AgentWrite` cannot be observed from outside today: its result does not say whether a
  create was replayed, nor the note's sensitivity. Its result DTO gains both (`replayed`, `sensitivity`). No rule of
  the write path changes, and neither field is added to the MCP response.

### Usage counters

- A new port, `UsageCounters`, backed by `usage.sqlite` in the state directory: `(token id, UTC hour, count)` and each
  token's last use. Rows older than 25 hours are removed on write.
- The file is not backed up and is listed with the files kept out of external backups. It is never migrated (ADR-0023):
  a file with an unknown schema or damaged content is discarded and recreated, at startup or when first found so.
  Failing to do either loses only counters and never stops the service.
- The write waits at most 100 ms for the database and is otherwise dropped.
- Only a `restricted` owner session reads the counters.

### Live channel

- A new port, `ActivityFeed`, held **in memory only** and lost on restart. Publishing from a worker thread never blocks:
  it hands the event to the server's event loop, which owns all feed state.
- `GET /api/activity` answers `text/event-stream` to a signed-in owner session with the ceiling **`restricted`**, on
  either listener, like change history (a write event is change history: ADR-0020, ADR-0029). It uses the same cookie
  and Host and Origin rules as the other owner reads and accepts no query parameters. The CSP keeps `connect-src 'self'`.
- Authentication comes first: a caller without a valid session gets 401 and holds no stream slot.
- **Ceiling.** The route is `restricted` only, and the feed still filters: an event is queued for a session only if the
  note was within its ceiling when the event happened, and sent only if an application use case, `VisibleActivity`,
  confirms the note is visible to that session now. A missing or unreadable note produces nothing. The filter runs
  before any bounded buffer, so a hidden event never takes the place of a visible one. Sensitivity is never sent.
- The feed retains the last 20 events. A new connection receives those that pass the same filter. There is no replay
  position. Each subscriber queue holds at most 20 visible events and drops its oldest.
- **Session.** Neither opening the stream nor keeping it renews the idle timeout, so automatic reconnection cannot keep
  a session alive: the route authenticates through a check that does not touch the session. The stream repeats it at
  least every 15 seconds and ends on logout, expiry or a password change.
- **Bounds.** At most 4 open streams per listener; a further one gets 429. A stream is closed after one hour and may
  reconnect. A comment line is sent on a fixed 15-second clock, independent of any event, to keep proxies from closing
  an idle stream.
- **Shutdown.** Streams watch the server's shutdown signal and end at once, and the server sets a graceful shutdown
  timeout of 5 seconds as a backstop.
- Nothing about events, sessions or tokens is logged.

### Browser

The feed and the pulse live on the graph screen, which closes the stream when it is left, hidden or signed out. Active
notes show their names. With `prefers-reduced-motion`, the node and edge are highlighted without animation. The feed
is a list that assistive technology can read; it is not announced on every event.

## Consequences

- The service keeps, for the first time, a record of which agent touched which note: in memory, at most 20 retained
  entries and the subscriber queues. Counters on disk name tokens and hours only.
- A public listener without the `restricted` browser opt-in has no live activity: its owner session is `internal`.
- Long-lived connections count against the server's concurrency bound; the stream limit keeps MCP unaffected.
- The browser authentication needs a session check that does not renew the idle timeout.
- `AgentWriteResult` carries two more fields.
- Activity is per process. Running several service processes would need a shared channel and a new decision.
- Events carry the time, beyond the note id, verb and client first named, as the approved data design does.

## Alternatives considered

- Polling: simpler, but delays the pulse and renews the idle session unless special-cased too.
- WebSocket: a second protocol and its own origin checks for a one-way feed.
- Persisting events: history of agent behaviour on disk and in backups, with no requirement for it.
- Counting inside or around `AuthenticateToken`: it would change token validation, which the owner excluded, or count one
  request several times.
- Serving the channel to lower-ceiling owner sessions: they may not read change history.
- Counters in `state.sqlite`: needs the first in-place schema upgrade (ADR-0023); rejected by the owner.
