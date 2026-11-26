import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./api";
import { markdownHeadings } from "./Markdown";
import { notePath, type Note } from "./Reader";
import "./search.css";

type Filter = "area" | "type" | "priority" | "status";
type Criteria = Record<Filter, string> & { q: string; history: boolean };
type NoteSummary = Record<Filter, string> & { id: string; title: string; summary: string };

const filters: Filter[] = ["area", "type", "priority", "status"];
const defaults: Criteria = { q: "", area: "", type: "", priority: "", status: "", history: false };
const historyStatuses = new Set(["archived", "superseded"]);
const searchState = ["search-form"];

function searchPath(criteria: Criteria) {
  const params = new URLSearchParams({ history: String(criteria.history) });
  for (const key of ["q", ...filters] as const) {
    if (criteria[key]) params.set(key, criteria[key]);
  }
  return "/api/search?" + params;
}

function NoteSections({api, note, history}: {api: ApiClient; note: NoteSummary; history: boolean}) {
  const query = useQuery({
    queryKey: ["note", note.id, history, null],
    queryFn: ({signal}) => api.read<Note>("/api" + notePath(note.id, history), signal),
    select: fullNote => markdownHeadings(fullNote.body),
  });
  if (query.isPending) return <p role="status">Loading sections…</p>;
  if (query.isError) return <div role="alert"><p>Sections are unavailable.</p>
    <button type="button" onClick={() => void query.refetch()}>Retry sections</button></div>;
  if (!query.data.length) return <p role="status">No sections in this note.</p>;
  return <nav className="search-sections" aria-label={`Sections of ${note.title}`}>
    {query.data.map((heading, index) => <a key={index} href={notePath(note.id, history, heading.title)}>{heading.title}</a>)}
  </nav>;
}

export function SearchScreen({api}: {api: ApiClient}) {
  const client = useQueryClient();
  const [criteria, setCriteria] = useState(() => client.getQueryData<Criteria>(searchState) ?? defaults);
  const [draft, setDraft] = useState(criteria);
  const [invalid, setInvalid] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const metadata = useQuery({
    queryKey: ["search-options", draft.history],
    queryFn: async ({signal}) => {
      const {nodes} = await api.read<{nodes: Record<Filter, string>[]}>("/api/graph?history=" + draft.history, signal);
      return Object.fromEntries(filters.map(key => [key, [...new Set(nodes.map(node => node[key]))].sort()])) as Record<Filter, string[]>;
    },
  });
  const results = useQuery({
    queryKey: ["search-results", criteria],
    queryFn: ({signal}) => api.read<{results: NoteSummary[]}>(searchPath(criteria), signal),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (new TextEncoder().encode(draft.q).length > 1024) {
      setInvalid(true);
      return;
    }
    const next = {...draft, q: draft.q.trim()};
    setInvalid(false);
    setExpanded(null);
    setCriteria(next);
    client.setQueryData(searchState, next);
  }

  return <article className="search-view">
    <header className="page-heading"><h1>Search</h1></header>
    <form role="search" className="search-form" onSubmit={submit}>
      <label htmlFor="note-query">Search notes</label>
      <div className="search-entry">
        <div><i aria-hidden="true" className="nav-icon icon-search"/>
          <input id="note-query" type="search" value={draft.q} autoComplete="off" placeholder="Search by keyword"
            aria-invalid={invalid} aria-describedby={invalid ? "query-error" : undefined}
            onChange={e => {setDraft({...draft, q: e.target.value}); setInvalid(false);}}/></div>
        <button type="submit" className="primary-btn">Search notes</button>
      </div>
      {invalid && <p id="query-error" role="alert">Search text must fit in 1,024 UTF-8 bytes.</p>}
      <div className="search-filters">{filters.map(key => {
        const label = key[0].toUpperCase() + key.slice(1);
        const options = metadata.data?.[key] ?? [];
        return <label key={key}>{label}
          <select value={draft[key]} onChange={e => setDraft({...draft, [key]: e.target.value})}>
            <option value="">Any {key}</option>
            {draft[key] && !options.includes(draft[key]) && <option value={draft[key]}>{draft[key]}</option>}
            {options.map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>;
      })}</div>
      <label className="search-history"><input type="checkbox" checked={draft.history} onChange={e => {
        const history = e.target.checked;
        setDraft({...draft, history, status: !history && historyStatuses.has(draft.status) ? "" : draft.status});
      }}/>Include history</label>
      {metadata.isError && <p role="alert">Filter options are unavailable. <button type="button" onClick={() => void metadata.refetch()}>Retry filters</button></p>}
    </form>
    <section className="search-results" aria-labelledby="results-heading">
      <div className="panel-heading"><h2 id="results-heading">Results</h2>
        {results.data && <span className="item-count">{results.data.results.length} shown</span>}</div>
      {results.isPending ? <p role="status">Searching…</p> :
        results.isError ? <div role="alert"><p>Search is unavailable.</p><button onClick={() => void results.refetch()}>Retry search</button></div> :
        results.data.results.length === 0 ? <p role="status">No matching notes.</p> :
        <ul>{results.data.results.map(note => <li key={note.id}>
          <div className="search-note-meta"><span>{note.type}</span><span>{note.area}</span>
            <span translate="no">{note.priority}</span><span>{note.status}</span></div>
          <h3><a href={notePath(note.id, criteria.history)}>{note.title}</a></h3>
          <p>{note.summary}</p>
          <button type="button" className="section-toggle" aria-expanded={expanded === note.id}
            aria-label={`Sections for ${note.title}`} onClick={() => setExpanded(expanded === note.id ? null : note.id)}>Sections</button>
          {expanded === note.id && <NoteSections api={api} note={note} history={criteria.history}/>}
        </li>)}</ul>}
    </section>
  </article>;
}
