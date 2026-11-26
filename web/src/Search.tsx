import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./api";
import { markdownHeadings, TechnicalText } from "./Markdown";
import { notePath, type Note } from "./Reader";
import { useVisibleGraph } from "./visibleGraph";
import "./search.css";

type Filter = "area" | "type" | "priority" | "status";
type Criteria = Record<Filter, string> & { q: string; history: boolean };
type NoteSummary = Record<Filter, string> & { id: string; title: string; summary: string };

const filters: Filter[] = ["area", "type", "priority", "status"];
const defaults: Criteria = { q: "", area: "", type: "", priority: "", status: "", history: false };
type Options = Record<Filter, string[]>;
function visibleFilters(draft: Criteria, options?: Options): Criteria {
  if (!options) return draft;
  let next = draft;
  for (const key of filters) {
    if (draft[key] && !options[key].includes(draft[key])) next = {...next, [key]: ""};
  }
  return next;
}
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
  const metadata = useVisibleGraph(api, draft.history);
  const options = useMemo(() => {
    if (!metadata.data) return undefined;
    const nodes = metadata.data.nodes;
    return Object.fromEntries(filters.map(key => [key, [...new Set(nodes.map(node => node[key]))].sort()])) as Options;
  }, [metadata.data]);
  useEffect(() => {
    if (options) setDraft(current => visibleFilters(current, options));
  }, [options]);
  const results = useQuery({
    queryKey: ["search-results", criteria],
    queryFn: ({signal}) => api.read<{results: NoteSummary[]}>(searchPath(criteria), signal),
  });

  const visibleIds = useMemo(() => {
    const notes = draft.history === criteria.history && metadata.data ? metadata.data.nodes : results.data?.results;
    return new Set(notes?.map(note => note.id));
  }, [draft.history, criteria.history, metadata.data, results.data]);

  const awaitingOptions = metadata.isPending && filters.some(key => draft[key] !== "");

  function submit(event: FormEvent) {
    event.preventDefault();
    if (awaitingOptions) return;
    if (new TextEncoder().encode(draft.q).length > 1024) {
      setInvalid(true);
      return;
    }
    const next = {...visibleFilters(draft, options), q: draft.q.trim()};
    setDraft(next);
    setInvalid(false);
    setExpanded(null);
    setCriteria(next);
    client.setQueryData(searchState, next);
  }

  const count = results.data?.results.length ?? 0;
  const status = results.isPending ? "Searching…" : results.isError ? "Search is unavailable." :
    count === 0 ? "No matching notes." : `${count} ${count === 1 ? "result" : "results"} shown.`;
  const description = [
    criteria.q && `Search text: ${criteria.q}.`,
    ...filters.filter(key => criteria[key]).map(key => `${key}: ${criteria[key]}.`),
    criteria.history && "Includes historical notes.",
  ].filter(Boolean).join(" ");
  return <article className="search-view">
    <header className="page-heading"><h1>Search</h1></header>
    <form role="search" className="search-form" onSubmit={submit}>
      <label htmlFor="note-query">Search notes</label>
      <div className="search-entry">
        <div><i aria-hidden="true" className="nav-icon icon-search"/>
          <input id="note-query" type="search" value={draft.q} autoComplete="off" placeholder="Search by keyword"
            aria-invalid={invalid} aria-describedby={invalid ? "query-error" : undefined}
            onChange={e => {setDraft({...draft, q: e.target.value}); setInvalid(false);}}/></div>
        <button type="submit" disabled={awaitingOptions} className="primary-btn">Search notes</button>
      </div>
      {invalid && <p id="query-error" role="alert">Search text must fit in 1,024 UTF-8 bytes.</p>}
      <div className="search-filters">{filters.map(key => {
        const label = key[0].toUpperCase() + key.slice(1);
        const values = options?.[key] ?? [];
        return <label key={key}>{label}
          <select value={draft[key]} onChange={e => setDraft({...draft, [key]: e.target.value})}>
            <option value="">Any {key}</option>
            {!options && draft[key] && <option value={draft[key]}>{draft[key]}</option>}
            {values.map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>;
      })}</div>
      <label className="search-history"><input type="checkbox" checked={draft.history} onChange={e => {
        const history = e.target.checked;
        setDraft({...draft, history});
      }}/>Include history</label>
      {metadata.isError && <p role="alert">Filter options are unavailable. <button type="button" onClick={() => void metadata.refetch()}>Retry filters</button></p>}
    </form>
    <section className="search-results" aria-labelledby="results-heading">
      <div className="panel-heading"><h2 id="results-heading">Results</h2>
        <span className="item-count" role="status" aria-atomic="true">{status}
          {description && <span className="search-announcement"> {description}</span>}</span></div>
      {results.isError && <button type="button" onClick={() => void results.refetch()}>Retry search</button>}
      {results.isSuccess && count > 0 &&
        <ul>{results.data.results.map(note => <li key={note.id}>
          <div className="search-note-meta"><span>{note.type}</span><span>{note.area}</span>
            <span translate="no">{note.priority}</span><span>{note.status}</span></div>
          <h3><a href={notePath(note.id, criteria.history)}><TechnicalText text={note.title} visibleIds={visibleIds}/></a></h3>
          <p><TechnicalText text={note.summary} visibleIds={visibleIds}/></p>
          <button type="button" className="section-toggle" aria-expanded={expanded === note.id}
            aria-label={`Sections for ${note.title}`} onClick={() => setExpanded(expanded === note.id ? null : note.id)}>Sections</button>
          {expanded === note.id && <NoteSections api={api} note={note} history={criteria.history}/>}
        </li>)}</ul>}
    </section>
  </article>;
}
