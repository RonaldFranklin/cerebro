import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApiClient } from "./api";
import { GraphCanvas } from "./GraphCanvas";
import { colorFor } from "./graphAppearance";
import { notePath } from "./Reader";
import { useVisibleGraph, type GraphData } from "./visibleGraph";
import "./graph.css";

const dimensions = ["area", "type", "priority", "status", "sensitivity"] as const;
type Dimension = typeof dimensions[number];
type Filters = Record<Dimension, string>;
type ColorBy = "area" | "type";
type View = { history: boolean; filters: Filters; colorBy: ColorBy };
type Neighborhood = { center: string; hops: 1 | 2; view: View };
type Workspace = { global: View; local: Neighborhood | null };

const initial: Workspace = {
  global: { history: false, filters: {area: "", type: "", priority: "", status: "", sensitivity: ""}, colorBy: "area" },
  local: null,
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

function Dot({value}: {value: string}) {
  return <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="5" fill={colorFor(value)}/></svg>;
}

export default function GraphScreen({api}: {api: ApiClient}) {
  const client = useQueryClient();
  const [workspace, setWorkspace] = useState(() => client.getQueryData<Workspace>(graphState) ?? initial);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const {global, local} = workspace;
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
  const titles = useMemo(() => new Map(graph?.nodes.map(node => [node.id, node.title])), [graph]);
  const legend = useMemo(() => [...new Set(graph?.nodes.map(node => node[view.colorBy]))].sort(), [graph, view.colorBy]);
  const center = local && graph?.nodes.find(node => node.id === local.center);
  useEffect(() => { client.setQueryData(graphState, workspace); }, [client, workspace]);
  function changeView(next: View) {
    setWorkspace(current => current.local
      ? {...current, local: {...current.local, view: next}}
      : {...current, global: next});
  }
  const explore = useCallback((id: string) => {
    setWorkspace(current => ({...current, local: {center: id, hops: 1, view: current.local?.view ?? current.global}}));
    titleRef.current?.focus();
  }, []);
  function back() {
    setWorkspace(current => ({...current, local: null}));
    titleRef.current?.focus();
  }

  return <article className="graph-view">
    <header className="page-heading graph-heading">
      <h1 ref={titleRef} tabIndex={-1}>Graph</h1>
      {local && <button className="graph-action" onClick={back}>Back to global graph</button>}
    </header>
    <div className="graph-controls">
      <div className="graph-filter-row">{dimensions.map(key =>
        <label key={key}>{key[0].toUpperCase() + key.slice(1)}
          <select value={view.filters[key]} onChange={event => changeView({...view, filters: {...view.filters, [key]: event.target.value}})}>
            <option value="">Any {key}</option>
            {view.filters[key] && !options[key].includes(view.filters[key]) && <option>{view.filters[key]}</option>}
            {options[key].map(value => <option translate="no" key={value}>{value}</option>)}
          </select>
        </label>)}</div>
      <div className="graph-settings">
        <label>Color by <select value={view.colorBy} onChange={event => changeView({...view, colorBy: event.target.value as ColorBy})}>
          <option value="area">Area</option><option value="type">Type</option>
        </select></label>
        <label><input type="checkbox" checked={view.history} onChange={event =>
          changeView({...view, history: event.target.checked})}/>Include history</label>
        {local && <fieldset><legend>Neighborhood depth</legend>{([1, 2] as const).map(hops =>
          <label key={hops}><input type="radio" name="depth" checked={local.hops === hops} onChange={() =>
            setWorkspace({...workspace, local: {...local, hops}})}/>{hops === 1 ? "1 hop" : "2 hops"}</label>)}</fieldset>}
      </div>
    </div>
    {local && catalog.isError && <p role="alert">Filter options are unavailable. <button onClick={() => void catalog.refetch()}>Retry filters</button></p>}
    {source.isError ? <p role="alert">{local ? "Neighborhood is unavailable." : "Graph is unavailable."} <button onClick={() => void source.refetch()}>Retry</button></p> :
      !graph ? <p role="status">Loading graph…</p> :
      <><section className="graph-map" aria-labelledby="map-title">
        <div className="graph-map-heading"><h2 id="map-title">{local ? center?.title ?? "Neighborhood" : "All notes"}</h2>
          <span role="status" className="item-count">{graph.nodes.length} notes · {graph.edges.length} links</span></div>
        {graph.nodes.length ? <GraphCanvas data={graph} colorBy={view.colorBy} onSelect={explore}/> : <p className="graph-empty">No matching notes.</p>}
        {legend.length > 0 && <div className="graph-legend" role="group" aria-label="Graph legend">
          <span>Color by {view.colorBy}</span>{legend.map(value => <span key={value}><Dot value={value}/><span translate="no">{value}</span></span>)}
          <span className="graph-size-key">Larger nodes have higher priority.</span>
        </div>}
      </section>
      <section className="graph-list" aria-labelledby="graph-notes"><h2 id="graph-notes">Notes</h2>
        {graph.edges.length > 0 && <details className="graph-connections">
          <summary>Connections</summary>
          <section aria-label="Graph connections">
            <ul>{graph.edges.map((edge, index) => <li key={index}>
              {titles.get(edge.source)} → {titles.get(edge.target)} · {edge.kind}{edge.section && ` · ${edge.section}`}
            </li>)}</ul>
          </section>
        </details>}
        <ul>{graph.nodes.map(node => <li key={node.id}>
          <Dot value={node[view.colorBy]}/><div><a href={notePath(node.id, view.history)}>{node.title}</a>
            <span translate="no">{node.area} · {node.priority}</span></div>
          <button type="button" className="graph-action" aria-label={`Explore ${node.title}`} onClick={() => explore(node.id)}>Explore</button>
        </li>)}</ul>
      </section></>}
  </article>;
}
