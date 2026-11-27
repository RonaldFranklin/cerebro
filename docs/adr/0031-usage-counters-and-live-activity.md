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

Both are wrappers added by the composition root around the use cases that the **MCP adapters** call. The use cases,
`AuthenticateToken` included, are unchanged, and the owner's browser and CLI raise nothing. A wrapper acts only after
the use case succeeded, never changes its result or its error, and drops its own failures.

| | Usage counter | Activity event |
|---|---|---|
| Raised when | a bearer credential authenticates | a note is read by `cerebro_read`, or created or updated by `cerebro_write` |
| Content | token id, time | note id, verb (`read` or `write`), agent name, time |
| Not raised by | refused credentials | overview, search, neighbors, refused or replayed writes |

### Usage counters

- A new port, `UsageCounters`, backed by `usage.sqlite` in the state directory: `(token id, UTC hour, count)` and each
  token's last use. Rows older than 25 hours are removed on write. The file is not backed up; a missing or damaged one
  loses only counters and never stops the service. `state.sqlite` is unchanged.
- The write waits at most a short busy timeout (100 ms) and is otherwise dropped.

### Live channel

- A new port, `ActivityFeed`, held **in memory only**: the last 20 events, lost on restart. Publishing never blocks:
  each subscriber has a bounded queue that drops its oldest event.
- `GET /api/activity` answers `text/event-stream` to a signed-in owner session on either listener, using the same
  cookie, Host and Origin rules as the other browser reads. It accepts no query parameters. The CSP keeps
  `connect-src 'self'`.
- **Ceiling.** An event is sent only if the note is within the session's ceiling both when the event happened and when
  it is delivered. A note that is above it, missing or unreadable produces nothing at all: no placeholder, counter or
  different timing signal in the stream. The stored sensitivity is never sent.
- A new connection first receives the retained events that pass the same filter. There is no replay position.
- **Session.** The stream checks its session at least every 15 seconds **without renewing the idle timeout**, and ends
  on logout, expiry or a password change. An open graph therefore does not keep a session alive.
- **Bounds.** At most 4 open streams per listener; a further one gets 429. A stream is closed after one hour and may
  reconnect. A comment line every 15 seconds keeps proxies from closing an idle stream. Shutdown closes all streams.
- Nothing about events, sessions or tokens is logged.

### Browser

The feed and the pulse live on the graph screen, which closes the stream when it is left, hidden or signed out. Active
notes show their names. With `prefers-reduced-motion`, the node and edge are highlighted without animation. The feed
is a list that assistive technology can read; it is not announced on every event.

## Consequences

- The service keeps, for the first time, a record of which agent touched which note: in memory, at most 20 entries.
  Counters on disk name tokens and hours only.
- A signed-in owner session sees agent activity within its ceiling, on the public listener too.
- Long-lived connections count against the server's concurrency bound; the stream limit keeps MCP unaffected.
- The browser authentication needs a session check that does not renew the idle timeout.
- Activity is per process. Running several service processes would need a shared channel and a new decision.

## Alternatives considered

- Polling: simpler, but delays the pulse and renews the idle session unless special-cased too.
- WebSocket: a second protocol and its own origin checks for a one-way feed.
- Persisting events: history of agent behaviour on disk and in backups, with no requirement for it.
- Counting inside `AuthenticateToken`: it would change token validation, which the owner excluded.
- Counters in `state.sqlite`: needs the first in-place schema upgrade (ADR-0023); rejected by the owner.
