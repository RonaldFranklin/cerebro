import { useNow, type Activity, type StreamState } from "./liveActivity";
import { notePath } from "./Reader";

function age(at: string, now: number) {
  const seconds = Math.max(0, (now - new Date(at).getTime()) / 1000);
  return seconds < 60 ? "now" : seconds < 3600 ? `${Math.floor(seconds / 60)} min` : `${Math.floor(seconds / 3600)} h`;
}

/** The latest agent reads and writes. A list to read when wanted: new entries are not announced. */
export function LiveActivity({events, state, retry}: {events: Activity[]; state: StreamState; retry: () => void}) {
  const now = useNow(15_000);
  return <section className="live-activity" aria-labelledby="live-title">
    <header><h2 id="live-title">Live activity</h2><span data-state={state}>{state}</span></header>
    {state === "unavailable" && <p>The activity stream is unavailable. <button type="button" onClick={retry}>Reconnect</button></p>}
    {events.length ? <ol>{events.map(event => <li key={`${event.at} ${event.verb} ${event.client} ${event.note_id}`}>
      <span className="live-verb" data-verb={event.verb}>{event.verb}</span>
      <span className="live-detail"><strong translate="no">{event.client}</strong>
        <a translate="no" href={notePath(event.note_id, true)}>{event.note_id}</a></span>
      <time dateTime={event.at}>{age(event.at, now)}</time>
    </li>)}</ol> : state !== "unavailable" && <p>No agent activity yet.</p>}
    <a href="/changes">All recent changes</a>
  </section>;
}
