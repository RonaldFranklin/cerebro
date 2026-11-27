import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, type ApiClient } from "./api";
import { author, dayKey, operations, type Operation } from "./changeFormat";

interface Change {revision: string; actor: string; timestamp: string; operation: Operation}

/** The committed changes of one note, newest first. The service searches a bounded window per page. */
export function NoteHistory({api, id}: {api: ApiClient; id: string}) {
  const cache = useQueryClient();
  const key = ["changes", "note", id];
  const history = useInfiniteQuery({
    queryKey: key,
    initialPageParam: null as string | null,
    queryFn: ({signal, pageParam}) => api.read<{changes: Change[]; next_cursor: string | null}>(
      `/api/notes/${encodeURIComponent(id)}/changes${pageParam ? `?cursor=${encodeURIComponent(pageParam)}` : ""}`, signal),
    getNextPageParam: page => page.next_cursor,
  });
  const changes = history.data?.pages.flatMap(page => page.changes) ?? [];
  // A refused or expired continuation starts again; another failure keeps what was read and can continue.
  // A failed refresh keeps the list already read.
  const restart = history.isError && (!history.data ||
    history.error instanceof ApiError && [401, 403, 404].includes(history.error.status));
  return <section className="note-history" aria-label="History">
    <h2>History</h2>
    {history.isPending ? <p role="status">Loading history…</p> : restart ? <>
      <p role="status">History is unavailable.</p>
      <button type="button" onClick={() => void cache.resetQueries({queryKey: key, exact: true})}>Retry history</button>
    </> : <>
      {changes.length > 0 ? <ol>{changes.map(change => <li key={change.revision}>
        <span>{operations[change.operation]} by <strong translate={change.actor === "owner" ? undefined : "no"}>{author(change.actor)}</strong></span>
        <span className="note-history-meta"><time dateTime={change.timestamp}>{dayKey(new Date(change.timestamp))}</time>
          {" · "}<code translate="no">{change.revision.slice(0, 7)}</code></span>
      </li>)}</ol> : <p>{history.hasNextPage ? "No changes to this note in the recent window." : "No changes to this note."}</p>}
      {history.isFetchNextPageError && <p role="status">Older changes are unavailable.</p>}
      {history.hasNextPage && <button type="button" disabled={history.isFetching}
        onClick={() => void history.fetchNextPage()}>Load older changes</button>}
    </>}
    <a href="/changes">View all changes</a>
  </section>;
}
