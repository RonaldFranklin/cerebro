import { useEffect, useState } from "react";

export interface Activity { note_id: string; verb: "read" | "write"; client: string; at: string }
export type StreamState = "connecting" | "streaming" | "unavailable";

const KEPT = 20;
const keyOf = (event: Activity) => `${event.at} ${event.verb} ${event.client} ${event.note_id}`;

function parse(data: string): Activity | null {
  try {
    const value = JSON.parse(data) as Partial<Activity>;
    return typeof value.note_id === "string" && (value.verb === "read" || value.verb === "write") &&
      typeof value.client === "string" && typeof value.at === "string" ? value as Activity : null;
  } catch {
    return null;
  }
}

/**
 * The owner's live activity stream while the screen is shown. The page closes it when hidden and when the screen is
 * left. The service never lets it renew the session, and nothing here asks the service for anything else.
 */
export function useLiveActivity(enabled: boolean) {
  const [events, setEvents] = useState<Activity[]>([]);
  const [state, setState] = useState<StreamState>("connecting");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let source: EventSource | undefined;
    function close() {
      source?.close();
      source = undefined;
    }
    function open() {
      if (source || document.visibilityState === "hidden") return;
      setState("connecting");
      const stream = new EventSource("/api/activity");
      source = stream;
      stream.onopen = () => setState("streaming");
      // The browser reconnects by itself after a dropped stream; a refused one stays closed.
      stream.onerror = () => setState(stream.readyState === EventSource.CLOSED ? "unavailable" : "connecting");
      stream.addEventListener("activity", message => {
        const event = parse((message as MessageEvent<string>).data);
        if (!event) return;
        // A new connection repeats the retained events.
        setEvents(current => current.some(item => keyOf(item) === keyOf(event)) ? current
          : [...current, event].sort((a, b) => b.at.localeCompare(a.at)).slice(0, KEPT));
      });
    }
    function visibility() {
      if (document.visibilityState === "hidden") close();
      else open();
    }
    document.addEventListener("visibilitychange", visibility);
    open();
    return () => { document.removeEventListener("visibilitychange", visibility); close(); };
  }, [enabled, attempt]);
  return {events, state, retry: () => setAttempt(count => count + 1)};
}

/** How long an event keeps its note highlighted in the graph. */
export const ACTIVE_FOR = 10_000;

/** The current time, renewed every ``interval`` while mounted; ``null`` stops the clock. */
export function useNow(interval: number | null) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (interval === null) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), interval);
    return () => window.clearInterval(timer);
  }, [interval]);
  return now;
}
