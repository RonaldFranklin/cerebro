import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./api";
import { GraphCanvas } from "./GraphCanvas";
import { colorFor } from "./graphAppearance";
import { LiveActivity } from "./LiveActivity";
import { ACTIVE_FOR, useLiveActivity, useNow } from "./liveActivity";
import { NotePanel } from "./NotePanel";
import { notePath } from "./Reader";
import { useVisibleGraph, type GraphData } from "./visibleGraph";
import "./graph.css";

const dimensions = ["area", "type", "priority", "status", "sensitivity"] as const;
type Dimension = typeof dimensions[number];
type Filters = Record<Dimension, string>;
type ColorBy = "area" | "type";
type View = { history: boolean; filters: Filters; colorBy: ColorBy };
type Neighborhood = { center: string; hops: 1 | 2; view: View };
type Workspace = { global: View; local: Neighborhood | null; selected: string };

const initial: Workspace = {
  global: { history: false, filters: {area: "", type: "", priority: "", status: "", sensitivity: ""}, colorBy: "area" },
  local: null,
  selected: "",
};
const graphState = ["graph-workspace"];

function filterGraph(data: GraphData, filters: Filters): GraphData {
  const nodes = data.nodes.filter(node => dimensions.every(key => !filters[key] || filters[key] === node[key]));
  const ids = new Set(nodes.map(node => node.id));
  return { ...data, nodes, edges: data.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target)) };
}

function neighborhoodPath(center: string, hops: number, view: View) {
  const params = new URLSearchParams({ history: String(view.history), hops: String(hops) });
  for (const key of dimensions) if (view.filters[key]) params.set(key, view.filters[key]);
  return "/api/notes/" + encodeURIComponent(center) + "/neighbors?" + params;
}

function Dot({color}: {color: string}) {
  return <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="5" fill={color}/></svg>;
}

function ConnectionList({graph}: {graph: GraphData}) {
  const titles = new Map(graph.nodes.map(node => [node.id, node.title]));
  const unique = new Set<string>();
  const repeated = new Set<string>();
  for (const title of titles.values()) {
    if (unique.has(title)) repeated.add(title);
    unique.add(title);
  }
  function endpoint(id: string) {
    const title = titles.get(id)!;
    return repeated.has(title) ? <>{title} (<span translate="no">{id}</span>)</> : title;
  }
  return <section aria-label="Graph connections"><ul>{graph.edges.map((edge, index) => <li key={index}>
    {endpoint(edge.source)} → {endpoint(edge.target)} · <span translate="no">{edge.kind}</span>{edge.section && ` · ${edge.section}`}
  </li>)}</ul></section>;
}

function Connections({graph}: {graph: GraphData}) {
  const [open, setOpen] = useState(false);
  return <details className="graph-connections" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Connections</summary>
    {open && <ConnectionList graph={graph}/>}
  </details>;
}

export default function GraphScreen({api, live}: {api: ApiClient; live: boolean}) {
  const activity = useLiveActivity(live);
  const now = useNow(live ? 1000 : null);
  // Notes an agent touched moments ago. The text key keeps the set's identity while its members stay.
  const activeKey = [...new Set(activity.events.filter(event => now - new Date(event.at).getTime() < ACTIVE_FOR)
    .map(event => event.note_id))].sort().join(" ");
  const active = useMemo(() => new Set(activeKey ? activeKey.split(" ") : []), [activeKey]);
  const client = useQueryClient();
  const [workspace, setWorkspace] = useState(() => client.getQueryData<Workspace>(graphState) ?? initial);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const {global, local, selected} = workspace;
  const view = local?.view ?? global;
  const catalog = useVisibleGraph(api, view.history);
  const options = useMemo(() => {
    const values = {} as Record<Dimension, string[]>;
    for (const key of dimensions) values[key] = [...new Set(catalog.data?.nodes.map(node => node[key]))].sort();
    return values;
  }, [catalog.data]);
  useEffect(() => {
    if (!catalog.data) return;
    setWorkspace(current => {
      const selection = current.local?.view ?? current.global;
      if (selection.history !== view.history) return current;
      const missing = dimensions.filter(key => selection.filters[key] && !options[key].includes(selection.filters[key]));
      if (!missing.length) return current;
      const filters = {...selection.filters};
      for (const key of missing) filters[key] = "";
      const next = {...selection, filters};
      return current.local ? {...current, local: {...current.local, view: next}} : {...current, global: next};
    });
  }, [catalog.data, options, view.history]);
  const localPath = local && neighborhoodPath(local.center, local.hops, view);
  const nearby = useQuery({
    queryKey: ["neighborhood", localPath],
    queryFn: ({signal}) => api.read<GraphData>(localPath!, signal),
    enabled: Boolean(localPath),
  });
  const source = local ? nearby : catalog;
  const graph = useMemo(() => source.data && filterGraph(source.data, view.filters), [source.data, view.filters]);
  const legend = useMemo(() => [...new Set(graph?.nodes.map(node => node[view.colorBy]))].sort(), [graph, view.colorBy]);
  // Colors come from every visible value, so filtering never recolors what stays. The shown graph joins the domain
  // because a neighborhood can answer before, or without, the catalog.
  const colorOf = useMemo(() => {
    const domain = [...catalog.data?.nodes ?? [], ...source.data?.nodes ?? []].map(node => node[view.colorBy]);
    return (value: string) => colorFor(value, domain);
  }, [catalog.data, source.data, view.colorBy]);
  const visibleIds = useMemo(() => new Set(catalog.data?.nodes.map(node => node.id)), [catalog.data]);
  const chosen = graph?.nodes.find(node => node.id === selected);
  // Each selection by the owner moves focus to the panel once, which also scrolls it into view.
  const [reveal, setReveal] = useState(false);
  const revealed = useCallback(() => setReveal(false), []);
  const select = useCallback((id: string) => {
    setWorkspace(current => ({...current, selected: id}));
    setReveal(true);
  }, []);
  const center = local && graph?.nodes.find(node => node.id === local.center);
  useEffect(() => { client.setQueryData(graphState, workspace); }, [client, workspace]);
  function changeView(next: View) {
    setWorkspace(current => current.local
      ? {...current, local: {...current.local, view: next}}
      : {...current, global: next});
  }
  const explore = useCallback((id: string) => {
    setWorkspace(current => ({...current, selected: id,
      local: {center: id, hops: 1, view: current.local?.view ?? current.global}}));
    titleRef.current?.focus();
  }, []);
  function back() {
    setWorkspace(current => ({...current, local: null}));
    titleRef.current?.focus();
  }

  return <article className="graph-view">
    <h1 className="visually-hidden" ref={titleRef} tabIndex={-1}>Graph</h1>
    <div className="graph-controls">
      {dimensions.map(key => {
        const name = key[0].toUpperCase() + key.slice(1);
        return <label key={key}><span className="visually-hidden">{name}</span>
          <select value={view.filters[key]} onChange={event => changeView({...view, filters: {...view.filters, [key]: event.target.value}})}>
            <option value="">{name}: all</option>
            {view.filters[key] && !options[key].includes(view.filters[key]) && <option value={view.filters[key]}>{name}: {view.filters[key]}</option>}
            {options[key].map(value => <option translate="no" key={value} value={value}>{name}: {value}</option>)}
          </select>
        </label>;
      })}
      <label><span className="visually-hidden">Color by</span>
        <select value={view.colorBy} onChange={event => changeView({...view, colorBy: event.target.value as ColorBy})}>
          <option value="area">Color: area</option><option value="type">Color: type</option>
        </select></label>
      <label className="graph-check"><input type="checkbox" checked={view.history} onChange={event =>
        changeView({...view, history: event.target.checked})}/>Include history</label>
      {local && <fieldset><legend>Neighborhood depth</legend>{([1, 2] as const).map(hops =>
        <label key={hops}><input type="radio" name="depth" checked={local.hops === hops} onChange={() =>
          setWorkspace({...workspace, local: {...local, hops}})}/>{hops === 1 ? "1 hop" : "2 hops"}</label>)}</fieldset>}
      {local && <button type="button" onClick={back}>Back to global graph</button>}
    </div>
    {local && catalog.isError && <p role="alert">Filter options are unavailable. <button onClick={() => void catalog.refetch()}>Retry filters</button></p>}
    {source.isError ? <p role="alert">{local ? "Neighborhood is unavailable." : "Graph is unavailable."} <button onClick={() => void source.refetch()}>Retry</button></p> :
      !graph ? <p role="status">Loading graph…</p> :
      <div className="graph-layout"><section className="graph-map" aria-labelledby="map-title">
        <div className="graph-map-heading"><h2 id="map-title">{local ? center?.title ?? "Neighborhood" : "All notes"}</h2>
          <span role="status" className="item-count">{graph.nodes.length} notes · {graph.edges.length} links</span></div>
        {graph.nodes.length ? <GraphCanvas data={graph} colorBy={view.colorBy} colorOf={colorOf} selected={selected} active={active} onSelect={select}/>
          : <p className="graph-empty">No matching notes.</p>}
        {legend.length > 0 && <div className="graph-legend" role="group" aria-label="Graph legend">
          <span>Color by {view.colorBy}</span>{legend.map(value => <span key={value}><Dot color={colorOf(value)}/><span translate="no">{value}</span></span>)}
          <span><svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" strokeWidth="2"/></svg>hub</span>
          <span className="graph-size-key">Larger nodes have higher priority.</span>
        </div>}
      </section>
      <div className="graph-side">
        <NotePanel api={api} note={chosen} color={chosen && colorOf(chosen[view.colorBy])} history={view.history}
          visibleIds={visibleIds} reveal={reveal} onRevealed={revealed} onExplore={local?.center === chosen?.id ? undefined : explore}/>
        {live && <LiveActivity events={activity.events} state={activity.state} retry={activity.retry}/>}
      </div>
      <section className="graph-list" aria-labelledby="graph-notes"><h2 id="graph-notes">Notes</h2>
        {graph.edges.length > 0 && <Connections graph={graph}/>}
        <ul>{graph.nodes.map(node => <li key={node.id}>
          <Dot color={colorOf(node[view.colorBy])}/><div><a href={notePath(node.id, view.history)}>{node.title}</a>
            <span translate="no">{node.area} · {node.priority}</span></div>
          <button type="button" className="graph-select" aria-label={`Select ${node.title}`}
            aria-current={node.id === selected ? "true" : undefined} onClick={() => select(node.id)}>Select</button>
          <button type="button" aria-label={`Explore ${node.title}`} onClick={() => explore(node.id)}>Explore</button>
        </li>)}</ul>
      </section></div>}
  </article>;
}
