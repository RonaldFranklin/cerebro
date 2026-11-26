import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ApiClient } from "./api";
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

function ChangeDetails({api, revision}: {api: ApiClient; revision: string}) {
  const result = useQuery({
    queryKey: ["changes", "detail", revision],
    queryFn: ({signal}) => api.read<ChangeDetail>(`/api/changes/${encodeURIComponent(revision)}`, signal),
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
  const [selected, setSelected] = useState<string | null>(null);
  const history = useQuery({
    queryKey: ["changes", "list"],
    queryFn: ({signal}) => api.read<{changes: Change[]}>("/api/changes", signal),
  });
  return <article className="changes-view">
    <header className="page-heading"><h1>Recent changes</h1><p>Inspect committed note edits and the differences between revisions.</p></header>
    {history.isPending ? <p role="status">Loading recent changes…</p>
      : history.isError ? <div><p role="alert">Recent changes are unavailable.</p>
        <button type="button" onClick={() => void history.refetch()}>Retry</button></div>
      : history.data.changes.length === 0 ? <p className="empty-state">No note changes to show.</p>
      : <ol className="change-list">{history.data.changes.map(change => {
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
          {expanded && <div className="change-expanded"><ChangeDetails api={api} revision={change.revision}/></div>}
        </li>;
      })}</ol>}
  </article>;
}
