import { useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, type ApiClient } from "./api";
import { notePath } from "./Reader";
import "./recentChanges.css";

interface Change {
  revision: string;
  note_id: string;
  title: string;
  actor: string;
  timestamp: string;
  operation: "create" | "update";
}

interface ChangeDetail extends Change {
  diff: string;
  truncated: boolean;
}

const dateFormat = new Intl.DateTimeFormat(undefined, {
  year: "numeric", month: "short", day: "numeric",
  hour: "2-digit", minute: "2-digit", timeZoneName: "short",
});

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
    <header><div><h2>Commit diff</h2><code translate="no">{revision}</code></div>
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
    queryFn: ({signal, pageParam}) => api.read<{changes: Change[]; next_cursor: string | null}>(
      pageParam ? `/api/changes?cursor=${encodeURIComponent(pageParam)}` : "/api/changes", signal),
    getNextPageParam: page => page.next_cursor,
  });
  const changes = history.data?.pages.flatMap((page, index) => {
    const cursor = history.data.pageParams[index];
    return page.changes.map(change => ({change, cursor: typeof cursor === "string" ? cursor : null}));
  }) ?? [];
  const denied = history.error instanceof ApiError && [401, 403].includes(history.error.status);
  const expired = history.isFetchNextPageError && history.error instanceof ApiError && history.error.status === 404;
  return <article className="changes-view">
    <header className="page-heading"><h1>Recent changes</h1><p>Inspect committed note edits and the differences between revisions.</p></header>
    {history.isPending ? <p role="status">Loading recent changes…</p>
      : history.isError && (!history.isFetchNextPageError || denied || expired) ? <div><p role="alert">Recent changes are unavailable.</p>
        <button type="button" onClick={() => void cache.resetQueries({queryKey:["changes", "list"], exact:true})}>Retry</button></div>
      : changes.length === 0 && !history.hasNextPage ? <p className="empty-state">No note changes to show.</p>
      : <ol className="change-list">{changes.map(({change, cursor}) => {
        const expanded = selected === change.revision;
        return <li key={change.revision}>
          <button type="button" className="change-row" aria-expanded={expanded}
            onClick={() => setSelected(expanded ? null : change.revision)}>
            <span className="change-operation">{change.operation === "create" ? "Created" : "Updated"}</span>
            <strong>{change.title}</strong>
            <span className="change-actor" translate="no">{change.actor}</span>
            <time dateTime={change.timestamp}>{dateFormat.format(new Date(change.timestamp))}</time>
            <i aria-hidden="true" className="change-chevron"/>
          </button>
          {expanded && <div className="change-expanded"><ChangeDetails api={api} revision={change.revision} cursor={cursor}/></div>}
        </li>;
      })}</ol>}
    {history.isFetchNextPageError && !denied && !expired && <p role="alert">Older changes are unavailable.</p>}
    {history.hasNextPage && !denied && !expired && <button type="button" disabled={history.isFetching}
      onClick={() => void history.fetchNextPage()}>Load more</button>}
  </article>;
}
