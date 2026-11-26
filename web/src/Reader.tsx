import { useQuery } from "@tanstack/react-query";
import { useMemo, type ReactNode } from "react";

import { ApiError, type ApiClient, type Ceiling } from "./api";
import { Markdown, markdownHeadings, TechnicalText } from "./Markdown";
import { useVisibleGraph } from "./visibleGraph";
import "./reader.css";

interface Member {
  id: string;
  title: string;
  type: string;
  priority: string;
  status: string;
  summary: string;
}

export interface Note extends Member {
  area: string;
  sensitivity: Ceiling;
  hub: string | null;
  related: string[];
  superseded_by: string | null;
  lang: string;
  created: string;
  updated: string;
  review_by: string | null;
  section: string | null;
  body: string;
  members: Member[] | null;
  content_hash?: string;
}

export function notePath(id: string, history: boolean, section?: string): string {
  const query = new URLSearchParams();
  if (history) query.set("history", "true");
  if (section) query.set("section", section);
  return `/notes/${encodeURIComponent(id)}${query.size ? "?" + query.toString() : ""}`;
}

function Meta({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd translate="no">{value}</dd></div>;
}

function Document({ note, history, outlineBody, outlineStatus, visibleIds }: {
  note: Note; history: boolean; outlineBody: string; outlineStatus?: ReactNode; visibleIds: ReadonlySet<string>;
}) {
  const headings = useMemo(() => markdownHeadings(outlineBody), [outlineBody]);
  const wholeNote = notePath(note.id, history);
  return <article className="reader-view">
    <nav className="reader-trail" aria-label="Breadcrumb">
      <a href="/search">Search</a><span aria-hidden="true">/</span>
      {note.hub && <><a translate="no" href={notePath(note.hub, history)}>{note.hub}</a><span aria-hidden="true">/</span></>}
      <span aria-current="page" translate="no">{note.id}</span>
    </nav>
    <header className="reader-heading" lang={note.lang}>
      <div className="reader-class"><span translate="no">{note.type}</span><span translate="no">{note.priority}</span></div>
      <h1><TechnicalText text={note.title} visibleIds={visibleIds}/></h1>
      <p><TechnicalText text={note.summary} visibleIds={visibleIds}/></p>
    </header>
    <div className="reader-columns">
      <div className="reader-document">
        {note.section && <p className="reader-section-return"><a href={wholeNote}>Read full note</a></p>}
        <Markdown body={note.body} lang={note.lang} history={history} visibleIds={visibleIds}/>
        {note.members !== null && <section className="reader-members" aria-labelledby="reader-members-title">
          <h2 id="reader-members-title">Hub members</h2>
          {note.members.length ? <ul>{note.members.map(member => <li key={member.id}>
            <a translate="no" href={notePath(member.id, history)}>{member.title}</a>
            <span translate="no">{member.type}</span><p><TechnicalText text={member.summary} visibleIds={visibleIds}/></p>
          </li>)}</ul> : <p>No visible members.</p>}
        </section>}
      </div>
      <aside className="reader-aside" aria-label="Note information">
        {outlineStatus}
        {headings.length > 0 && <nav aria-label="Sections"><h2>Sections</h2><ul>{headings.map((heading, index) =>
          <li key={index}><a translate="no" href={notePath(note.id, history, heading.title)}
            aria-current={note.section === heading.title ? "location" : undefined}>{heading.title}</a></li>
        )}</ul></nav>}
        <section><h2>Details</h2><dl>
          <Meta label="Area" value={note.area}/><Meta label="Status" value={note.status}/>
          <Meta label="Sensitivity" value={note.sensitivity}/><Meta label="Updated" value={note.updated}/>
          {note.review_by && <Meta label="Review by" value={note.review_by}/>}
        </dl></section>
        {(note.related.length > 0 || note.superseded_by) && <section><h2>Related notes</h2><ul>
          {note.superseded_by && <li>Replaced by <a translate="no" href={notePath(note.superseded_by, history)}>{note.superseded_by}</a></li>}
          {note.related.map(id => <li key={id}><a translate="no" href={notePath(id, history)}>{id}</a></li>)}
        </ul></section>}
      </aside>
    </div>
  </article>;
}

export function ReaderScreen({ api, id, history, section }: {
  api: ApiClient; id: string; history: boolean; section: string | null;
}) {
  const full = useQuery({
    queryKey: ["note", id, history, null],
    queryFn: ({ signal }) => api.read<Note>("/api" + notePath(id, history), signal),
  });
  const part = useQuery({
    enabled: Boolean(section),
    queryKey: ["note", id, history, section],
    queryFn: ({ signal }) => api.read<Note>("/api" + notePath(id, history, section ?? undefined), signal),
  });
  const result = section ? part : full;
  const graph = useVisibleGraph(api, history);
  const visibleIds = useMemo(() => new Set(graph.data?.nodes.map(node => node.id)), [graph.data]);
  if (result.isPending) return <p role="status">Loading note…</p>;
  if (result.isError) {
    const missing = result.error instanceof ApiError && result.error.status === 404;
    return <section className="pending-view">
      <h1>{missing ? "Note unavailable" : "Could not load note"}</h1>
      <p role="alert">{missing ? "This note or section is unavailable." : "The service could not complete the request."}</p>
      {!missing && <button onClick={() => void result.refetch()}>Retry</button>}
      {section && <a href={notePath(id, history)}>Read full note</a>} <a href="/search">Back to search</a>
    </section>;
  }
  const outlineStatus = section && !full.data && (full.isError
    ? <div><p role="status">Could not load sections.</p><button onClick={() => void full.refetch()}>Retry sections</button></div>
    : <p role="status">Loading sections…</p>);
  return <Document note={result.data} history={history} outlineBody={full.data?.body ?? result.data.body}
    outlineStatus={outlineStatus} visibleIds={visibleIds}/>;
}
