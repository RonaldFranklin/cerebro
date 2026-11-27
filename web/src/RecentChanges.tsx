import { useEffect, useMemo, useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, type ApiClient } from "./api";
import { notePath } from "./Reader";
import { author, dayKey, operations, type Operation } from "./changeFormat";
import { colorFor } from "./graphAppearance";
import { useVisibleGraph } from "./visibleGraph";
import "./recentChanges.css";

interface Change {
  revision: string;
  note_id: string;
  title: string;
  actor: string;
  timestamp: string;
  operation: Operation;
}

interface ChangeDetail extends Change {
  diff: string;
  truncated: boolean;
}

// The interface is English only, so dates and times are too.
const dayFormat = new Intl.DateTimeFormat("en", {weekday: "long", year: "numeric", month: "long", day: "numeric"});
const timeFormat = new Intl.DateTimeFormat("en", {hour: "2-digit", minute: "2-digit", hourCycle: "h23"});

function dayName(key: string, today: Date) {
  if (key === dayKey(today)) return "Today";
  if (key === dayKey(new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1))) return "Yesterday";
  const [year, month, day] = key.split("-").map(Number);
  return dayFormat.format(new Date(year, month - 1, day));
}

/** The current date, renewed at local midnight and when the page returns, so open screens keep correct headings. */
function useToday() {
  const [today, setToday] = useState(() => new Date());
  useEffect(() => {
    const renew = () => setToday(current => dayKey(current) === dayKey(new Date()) ? current : new Date());
    const midnight = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
    const timer = window.setTimeout(() => setToday(new Date()), Math.max(midnight.getTime() - Date.now(), 1000));
    document.addEventListener("visibilitychange", renew);
    return () => { window.clearTimeout(timer); document.removeEventListener("visibilitychange", renew); };
  }, [today]);
  return today;
}

function ChangeDetails({api, revision, cursor}: {api: ApiClient; revision: string; cursor: string | null}) {
  const result = useQuery({
    queryKey: ["changes", "detail", revision, cursor],
    queryFn: ({signal}) => api.read<ChangeDetail>(`/api/changes/${encodeURIComponent(revision)}${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`, signal),
    gcTime: 0,
  });
  if (result.isPending) return <p role="status">Loading change details…</p>;
  if (result.isError) return <div><p role="alert">Change details are unavailable.</p>
    <button type="button" onClick={() => void result.refetch()}>Retry</button></div>;
  const {diff, note_id, truncated} = result.data;
  return <section aria-label="Commit diff" className="change-detail">
    <header><div><h3>Commit diff</h3><code translate="no">{revision}</code></div>
      <a href={notePath(note_id, true)}>Open note</a></header>
    {truncated && <p>Only part of this diff is shown.</p>}
    {diff ? <pre tabIndex={0} aria-label="Diff text"><code translate="no">{diff.split("\n").map((line, index, lines) => {
      const tone = line.startsWith("+") && !line.startsWith("+++") ? "added"
        : line.startsWith("-") && !line.startsWith("---") ? "removed"
        : line.startsWith("@@") ? "hunk" : undefined;
      return <span data-tone={tone} key={index}>{line}{index < lines.length - 1 ? "\n" : ""}</span>;
    })}</code></pre> : <p>No textual differences to display.</p>}
  </section>;
}

export function RecentChangesScreen({api}: {api: ApiClient}) {
  const cache = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const history = useInfiniteQuery({
    queryKey: ["changes", "list"],
    initialPageParam: null as string | null,
    queryFn: ({signal, pageParam}) => api.read<{changes: Change[]; next_cursor: string | null; page_cursor: string | null}>(
      pageParam ? `/api/changes?cursor=${encodeURIComponent(pageParam)}` : "/api/changes", signal),
    getNextPageParam: page => page.next_cursor,
  });
  const changes = history.data?.pages.flatMap(page =>
    page.changes.map(change => ({change, cursor: page.page_cursor}))) ?? [];
  const denied = history.error instanceof ApiError && [401, 403].includes(history.error.status);
  const expired = history.isFetchNextPageError && history.error instanceof ApiError && history.error.status === 404;
  const graph = useVisibleGraph(api, true);
  const areas = useMemo(() => new Map(graph.data?.nodes.map(node => [node.id, node.area])), [graph.data]);
  const today = useToday();
  const [actor, setActor] = useState("");
  // Authors present in the loaded changes; the owner leads.
  const actors = [...new Set([...changes.map(({change}) => change.actor), ...actor ? [actor] : []])]
    .sort((a, b) => Number(b === "owner") - Number(a === "owner") || a.localeCompare(b));
  const shown = changes.filter(({change}) => !actor || change.actor === actor);
  // History order is Git order: a clock correction can return to an earlier day, which keeps a single heading.
  const days = new Map<string, typeof shown>();
  for (const row of shown) {
    const key = dayKey(new Date(row.change.timestamp));
    days.set(key, [...days.get(key) ?? [], row]);
  }
  return <article className="changes-view">
    <header className="changes-heading"><div><h1>Recent changes</h1>
      <p>Every write is a Git commit, attributed to the token or the owner that made it.</p></div>
      {actors.length > 0 && !denied && <div className="change-authors" role="group" aria-label="Filter by author">
        {["", ...actors].map(name => <button key={name} type="button" aria-pressed={actor === name} onClick={() => setActor(name)}>
          {name ? <span translate={name === "owner" ? undefined : "no"}>{author(name)}</span> : "All"}</button>)}
      </div>}</header>
    {history.isPending ? <p role="status">Loading recent changes…</p>
      : history.isError && (!history.isFetchNextPageError || denied || expired) ? <div><p role="alert">Recent changes are unavailable.</p>
        <button type="button" onClick={() => void cache.resetQueries({queryKey:["changes", "list"], exact:true})}>Retry</button></div>
      : changes.length === 0 && !history.hasNextPage ? <p className="empty-state">No note changes to show.</p>
      : actor && shown.length === 0 ? <p className="empty-state">No loaded changes by this author.</p>
      : [...days].map(([key, rows]) => <section key={key} className="change-day"><h2>{dayName(key, today)}</h2><ol className="change-list">
        {rows.map(({change, cursor}) => {
          const expanded = selected === change.revision;
          const area = areas.get(change.note_id);
          return <li key={change.revision}>
            <button type="button" className="change-row" aria-expanded={expanded}
              onClick={() => setSelected(expanded ? null : change.revision)}>
              <time dateTime={change.timestamp}>{timeFormat.format(new Date(change.timestamp))}</time>
              <span className="change-operation" data-operation={change.operation}>{operations[change.operation]}</span>
              <span className="change-area">{area && <><svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                <circle cx="5" cy="5" r="5" fill={colorFor(area, areas.values())}/></svg><span className="visually-hidden" translate="no">{area}</span></>}</span>
              <span className="change-note"><strong>{change.title}</strong><span translate="no">{change.note_id}</span></span>
              <span className="change-actor" translate={change.actor === "owner" ? undefined : "no"}>{author(change.actor)}</span>
              <code translate="no">{change.revision.slice(0, 7)}</code>
            </button>
            {expanded && <div className="change-expanded"><ChangeDetails api={api} revision={change.revision} cursor={cursor}/></div>}
          </li>;
        })}</ol></section>)}
    {history.isFetchNextPageError && !denied && !expired && <p role="alert">Older changes are unavailable.</p>}
    {history.hasNextPage && !denied && !expired && <button type="button" disabled={history.isFetching}
      onClick={() => void history.fetchNextPage()}>Load more</button>}
  </article>;
}
