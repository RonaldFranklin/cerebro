import { useQuery } from "@tanstack/react-query";

import type { ApiClient } from "./api";
import { TechnicalText } from "./Markdown";
import { notePath, type Note } from "./Reader";
import type { GraphNode } from "./visibleGraph";

/** The note selected in the graph. The graph carries its description; the note read adds the date and editability. */
export function NotePanel({api, note, color, history, visibleIds, onExplore}: {
  api: ApiClient; note: GraphNode | undefined; color: string | undefined; history: boolean;
  visibleIds: ReadonlySet<string>; onExplore: (id: string) => void;
}) {
  const full = useQuery({
    enabled: Boolean(note),
    queryKey: ["note", note?.id, history, null],
    queryFn: ({signal}) => api.read<Note>("/api" + notePath(note!.id, history), signal),
  });
  return <aside className="note-panel" aria-label="Selected note">
    {!note ? <p className="note-panel-empty">Select a note in the graph or the list to see its details.</p> : <>
      <p className="note-panel-class"><svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><circle cx="5" cy="5" r="5" fill={color}/></svg>
        <span translate="no">{note.area} · {note.type}</span></p>
      <h2><TechnicalText text={note.title} visibleIds={visibleIds}/></h2>
      <p className="note-panel-id" translate="no">{note.id}</p>
      <p><TechnicalText text={note.summary} visibleIds={visibleIds}/></p>
      <ul className="note-badges" aria-label="Note properties">
        <li translate="no">{note.priority}</li><li translate="no">{note.sensitivity}</li>
        <li translate="no" data-status={note.status}>{note.status}</li>
        {full.data && <li>updated <time dateTime={full.data.updated}>{full.data.updated}</time></li>}
      </ul>
      {full.isError && <p role="alert">Some details are unavailable. <button type="button" onClick={() => void full.refetch()}>Retry details</button></p>}
      <div className="note-panel-actions">
        <a className="primary-btn" href={notePath(note.id, history)}>Open note</a>
        {full.data?.content_hash && <a className="secondary-btn" href={`/editor/${encodeURIComponent(note.id)}${history ? "?history=true" : ""}`}>Edit</a>}
        <button type="button" className="secondary-btn" onClick={() => onExplore(note.id)}>Show neighborhood</button>
      </div>
    </>}
  </aside>;
}
