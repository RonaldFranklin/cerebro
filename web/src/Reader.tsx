import { useQuery } from "@tanstack/react-query";
import { useMemo, useState, type ReactNode } from "react";

import { ApiError, type ApiClient, type Ceiling } from "./api";
import { Markdown, markdownHeadings, TechnicalText } from "./Markdown";
import { useVisibleGraph } from "./visibleGraph";
import { Icon } from "./icons";
import { NoteLinks } from "./NoteLinks";
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

function Property({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return <div><dt>{label}</dt><dd translate="no" data-tone={tone}>{value}</dd></div>;
}

function CopyId({ id }: { id: string }) {
  const [message, setMessage] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(id);
      setMessage("Note id copied.");
    } catch {
      setMessage("The note id could not be copied.");
    }
  }
  return <>
    <button type="button" className="copy-id" aria-label="Copy note id" onClick={() => void copy()}><Icon name="copy"/></button>
    <span role="status" className="copy-status">{message}</span>
  </>;
}

function Document({ api, note, history, outlineBody, outlineStatus, visibleIds, areas, hubTitle }: {
  api: ApiClient; note: Note; history: boolean; outlineBody: string; outlineStatus?: ReactNode;
  visibleIds: ReadonlySet<string>; areas: string[]; hubTitle: string | undefined;
}) {
  const headings = useMemo(() => markdownHeadings(outlineBody), [outlineBody]);
  const wholeNote = notePath(note.id, history);
  return <article className="reader-view">
    <div className="reader-main">
      <nav className="reader-trail" aria-label="Breadcrumb">
        <span translate="no">{note.area}</span><span aria-hidden="true">/</span>
        {note.hub && <><a href={notePath(note.hub, history)}>
          {hubTitle ? <TechnicalText text={hubTitle} visibleIds={visibleIds}/> : <span translate="no">{note.hub}</span>}
        </a><span aria-hidden="true">/</span></>}
        <span aria-current="page" lang={note.lang}><TechnicalText text={note.title} visibleIds={visibleIds}/></span>
      </nav>
      <header className="reader-heading" lang={note.lang}>
        <div>
          <h1><TechnicalText text={note.title} visibleIds={visibleIds}/></h1>
          <p className="reader-id" translate="no">{note.id}</p>
        </div>
        <div className="reader-actions">
          {note.content_hash && !note.section && <a className="primary-btn edit-note-link"
            href={`/editor/${encodeURIComponent(note.id)}${history ? "?history=true" : ""}`}>Edit note</a>}
          <CopyId id={note.id}/>
        </div>
      </header>
      <section aria-label="Note properties"><dl className="reader-properties">
        <Property label="Type · priority" value={`${note.type} · ${note.priority}`}/>
        <Property label="Sensitivity" value={note.sensitivity} tone={note.sensitivity === "restricted" ? "warning" : undefined}/>
        <Property label="Status" value={note.status} tone={note.status === "current" || note.status === "verified" ? "ok" : undefined}/>
        <Property label="Updated" value={note.updated}/>
        {note.review_by && <Property label="Review by" value={note.review_by}/>}
      </dl></section>
      <p className="reader-summary" lang={note.lang}><TechnicalText text={note.summary} visibleIds={visibleIds}/></p>
      {note.superseded_by && <p className="reader-replaced">Replaced by <a translate="no" href={notePath(note.superseded_by, history)}>{note.superseded_by}</a></p>}
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
    </div>
    <aside className="reader-aside" aria-label="Note information">
      {outlineStatus}
      {headings.length > 0 && <nav aria-label="Sections"><h2>Sections</h2><ul>{headings.map((heading, index) =>
        <li key={index}><a translate="no" href={notePath(note.id, history, heading.title)}
          aria-current={note.section === heading.title ? "location" : undefined}>{heading.title}</a></li>
      )}</ul></nav>}
      <NoteLinks api={api} id={note.id} history={history} areas={areas} visibleIds={visibleIds}/>
    </aside>
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
  const hub = graph.data?.nodes.find(node => node.id === result.data.hub);
  return <Document key={result.data.id} api={api} note={result.data} history={history} outlineBody={full.data?.body ?? result.data.body}
    outlineStatus={outlineStatus} visibleIds={visibleIds} areas={graph.data?.nodes.map(node => node.area) ?? []} hubTitle={hub?.title}/>;
}
