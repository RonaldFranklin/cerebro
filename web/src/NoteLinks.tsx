import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import type { ApiClient } from "./api";
import { colorFor } from "./graphAppearance";
import { TechnicalText } from "./Markdown";
import { notePath } from "./Reader";
import type { GraphData, GraphNode } from "./visibleGraph";

const SHOWN = 8;
const WIDTH = 320, HEIGHT = 230;

function short(title: string) {
  return title.length > 22 ? title.slice(0, 21) + "…" : title;
}

/** A drawing of the direct links. The lists beside it carry the same information as text. */
function Neighborhood({center, neighbors, color}: {center: GraphNode; neighbors: GraphNode[]; color: (area: string) => string}) {
  const shown = neighbors.slice(0, SHOWN);
  const middle = [WIDTH / 2, HEIGHT / 2];
  const placed = shown.map((node, i) => {
    const angle = Math.PI + i / shown.length * 2 * Math.PI + .5;
    return {node, x: middle[0] + 105 * Math.cos(angle), y: middle[1] + 78 * Math.sin(angle)};
  });
  return <svg className="neighborhood" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} aria-hidden="true" focusable="false">
    {placed.map(({node, x, y}) => <line key={node.id} x1={middle[0]} y1={middle[1]} x2={x} y2={y}/>)}
    {placed.map(({node, x, y}) => <g key={node.id}>
      <circle data-note={node.id} className={node.type === "hub" ? "hub" : undefined} cx={x} cy={y} r={node.type === "hub" ? 8 : 5} fill={color(node.area)}/>
      <text x={x} y={y < middle[1] ? y - 13 : y + 19} textAnchor="middle">{short(node.title)}</text>
    </g>)}
    <circle className="halo" cx={middle[0]} cy={middle[1]} r="14"/>
    <circle data-note={center.id} cx={middle[0]} cy={middle[1]} r="8" fill={color(center.area)}/>
    <text x={middle[0]} y={middle[1] + 28} textAnchor="middle">this note</text>
    {neighbors.length > shown.length && <text x={WIDTH - 8} y={HEIGHT - 8} textAnchor="end">+{neighbors.length - shown.length} more</text>}
  </svg>;
}

function LinkList({title, notes, history, visibleIds}: {
  title: string; notes: GraphNode[]; history: boolean; visibleIds: ReadonlySet<string>;
}) {
  return <section className="note-links" aria-label={title}>
    <h2>{title} · {notes.length}</h2>
    {notes.length ? <ul>{notes.map(node => <li key={node.id}>
      <a href={notePath(node.id, history)}><TechnicalText text={node.title} visibleIds={visibleIds}/></a>
      <span translate="no">{node.type}</span>
    </li>)}</ul> : <p>No visible links.</p>}
  </section>;
}

export function NoteLinks({api, id, history, areas, visibleIds}: {
  api: ApiClient; id: string; history: boolean; areas: string[]; visibleIds: ReadonlySet<string>;
}) {
  const path = `/api/notes/${encodeURIComponent(id)}/neighbors?history=${history}&hops=1`;
  const nearby = useQuery({queryKey: ["neighborhood", path], queryFn: ({signal}) => api.read<GraphData>(path, signal)});
  const links = useMemo(() => {
    const data = nearby.data;
    const center = data?.nodes.find(node => node.id === id);
    if (!data || !center) return undefined;
    const ends = (from: "source" | "target", to: "source" | "target") => {
      const ids = new Set(data.edges.filter(edge => edge[from] === id).map(edge => edge[to]));
      return data.nodes.filter(node => node.id !== id && ids.has(node.id));
    };
    const out = ends("source", "target"), back = ends("target", "source");
    return {center, out, back, neighbors: [...new Set([...out, ...back])]};
  }, [nearby.data, id]);
  if (nearby.isPending) return <p role="status">Loading links…</p>;
  if (nearby.isError || !links) return <div><p role="status">Links are unavailable.</p>
    <button type="button" onClick={() => void nearby.refetch()}>Retry links</button></div>;
  const domain = [...areas, ...nearby.data.nodes.map(node => node.area)];
  return <>
    <section aria-label="Neighborhood"><h2>Neighborhood</h2>
      <Neighborhood center={links.center} neighbors={links.neighbors} color={area => colorFor(area, domain)}/></section>
    <LinkList title="Links out" notes={links.out} history={history} visibleIds={visibleIds}/>
    <LinkList title="Linked from" notes={links.back} history={history} visibleIds={visibleIds}/>
  </>;
}
