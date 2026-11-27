import Graph from "graphology";
import type Sigma from "sigma";
import { useLayoutEffect, useRef, useState } from "react";
import type { GraphData } from "./visibleGraph";
import type { LayoutInput } from "./graphLayout";
import { sizeFor } from "./graphAppearance";
import "./graphCanvas.css";

interface Props {
  data: GraphData;
  colorBy: "area" | "type";
  colorOf: (value: string) => string;
  selected: string;
  active: ReadonlySet<string>;
  onSelect: (id: string) => void;
}

/** Where the pulses of active notes are drawn, in viewport pixels. */
interface Pulse { id: string; x: number; y: number; to: { x: number; y: number } | null }

/** Names are drawn for hubs, the selected note and notes with live activity; hubs carry a white ring. */
function emphasis(hub: boolean, selected: boolean, active: boolean) {
  return {
    labelVisibility: hub || selected || active ? "visible" : "hidden",
    ringVisibility: hub || selected ? "visible" : "hidden",
    ringColor: selected ? "#5ce1e6" : "#e6edf5",
    // The selection also differs in shape, not only in color: a wider ring further out.
    ringWidth: selected ? 3 : 2,
    ringPadding: selected ? 5 : 2,
  };
}

export function GraphCanvas({data, colorBy, colorOf, selected, active, onSelect}: Props) {
  const [pulses, setPulses] = useState<Pulse[]>([]);
  const lively = useRef(active);
  const place = useRef<() => void>(() => {});
  const surface = useRef<HTMLDivElement>(null);
  const renderer = useRef<Sigma | null>(null);
  const select = useRef(onSelect);
  const color = useRef({colorBy, colorOf});
  const chosen = useRef(selected);
  const [status, setStatus] = useState<"loading" | "ready" | "fallback">("loading");

  useLayoutEffect(() => { select.current = onSelect; }, [onSelect]);
  useLayoutEffect(() => {
    color.current = {colorBy, colorOf};
    chosen.current = selected;
    lively.current = active;
    const graph = renderer.current?.getGraph();
    if (graph) graph.updateEachNodeAttributes((_key, attrs) => ({...attrs, color: colorOf(attrs[colorBy]),
      ...emphasis(attrs.type === "hub", attrs.id === selected, active.has(attrs.id))}));
    place.current();
  }, [colorBy, colorOf, selected, active]);

  useLayoutEffect(() => {
    const host = surface.current;
    if (!host) return;
    let alive = true;
    let worker: Worker | undefined;
    let sigma: Sigma | undefined;
    let resize: ResizeObserver | undefined;
    function dispose() {
      worker?.terminate();
      resize?.disconnect();
      sigma?.kill();
      sigma = undefined;
      renderer.current = null;
      place.current = () => {};
      setPulses([]);
      host?.replaceChildren();
    }
    function fallback() {
      if (!alive) return;
      dispose();
      setStatus("fallback");
    }
    setStatus("loading");
    try {
      worker = new Worker(new URL("./graphLayout.worker.ts", import.meta.url), {type:"module"});
      worker.onerror = fallback;
      worker.onmessageerror = fallback;
      const indexes = new Map(data.nodes.map((node, i) => [node.id, i]));
      const edges: LayoutInput["edges"] = [];
      for (const edge of data.edges) {
        const a = indexes.get(edge.source), b = indexes.get(edge.target);
        if (a !== undefined && b !== undefined) edges.push([a, b]);
      }
      worker.onmessage = async ({data: positions}: MessageEvent<Float32Array>) => {
        worker?.terminate();
        worker = undefined;
        try {
          const {default: SigmaRenderer} = await import("sigma");
          if (!alive) return;
          if (positions.length !== data.nodes.length * 2 || !positions.every(Number.isFinite)) { fallback(); return; }
          const graph = new Graph({multi:true});
          data.nodes.forEach((node, i) => graph.addNode(i, {
            x:positions[i * 2], y:positions[i * 2 + 1], label:node.title, area:node.area, type:node.type, id:node.id,
            color:color.current.colorOf(node[color.current.colorBy]), size:sizeFor(node.priority),
            ...emphasis(node.type === "hub", node.id === chosen.current, lively.current.has(node.id)),
          }));
          for (const [a,b] of edges) graph.addEdge(a,b);
          sigma = new SigmaRenderer(graph, host, {
            settings: {
              itemSizesReference: "screen", zoomToSizeRatioFunction: () => 1,
              stagePadding: 48, labelRenderedSizeThreshold: 3, labelDensity: 1,
              allowInvalidContainer: true, enableNodeDrag: false, minCameraRatio: .05, maxCameraRatio: 8,
            },
            styles: {
              nodes: {
                label: {attribute:"label"}, color:{attribute:"color"}, size:{attribute:"size"},
                labelColor:"#e6edf5", labelSize:13, labelPosition:"below", cursor:"pointer",
                labelVisibility:{attribute:"labelVisibility"},
                backdropVisibility:{attribute:"ringVisibility"}, backdropArea:"node", backdropColor:"rgba(0, 0, 0, 0)",
                backdropShadowBlur:0, backdropPadding:{attribute:"ringPadding"}, backdropBorderWidth:{attribute:"ringWidth"},
                backdropBorderColor:{attribute:"ringColor"},
              },
              edges: { color:"#5a6b82", size:1.3 },
            },
          });
          renderer.current = sigma;
          const shown = sigma;
          place.current = () => {
            if (!alive) return;
            const at = (key: number) => shown.graphToViewport(
              {x: graph.getNodeAttribute(key, "x") as number, y: graph.getNodeAttribute(key, "y") as number});
            const next = [...lively.current].flatMap(id => {
              const key = indexes.get(id);
              if (key === undefined) return [];
              // The synapse to the note's hub pulses when it has one, else to another neighbor.
              const near = edges.flatMap(([a, b]) => a === key ? [b] : b === key ? [a] : []);
              const other = near.find(index => data.nodes[index].type === "hub") ?? near[0];
              return [{id, ...at(key), to: other === undefined ? null : at(other)}];
            });
            setPulses(current => current.length === 0 && next.length === 0 ? current : next);
          };
          sigma.on("afterRender", () => place.current());
          // Events can arrive before the layout is ready, and the first frame precedes this listener.
          place.current();
          sigma.on("webglContextLost", () => { if (alive) setStatus("fallback"); });
          sigma.on("webglContextRestored", () => { if (alive) setStatus("ready"); });
          sigma.on("clickNode", ({node}) => select.current(data.nodes[Number(node)].id));
          resize = new ResizeObserver(() => { if (alive) sigma?.resize(); });
          resize.observe(host);
          setStatus("ready");
        } catch { fallback(); }
      };
      worker.postMessage({count:data.nodes.length, edges} satisfies LayoutInput);
    } catch { fallback(); }
    return () => { alive = false; dispose(); };
  }, [data]);

  return <div className="graph-canvas" data-renderer={status}>
    <div className="sigma-surface" ref={surface} aria-hidden="true"/>
    {pulses.length > 0 && <svg className="graph-pulses" aria-hidden="true" focusable="false">
      {pulses.map(pulse => <g key={pulse.id} data-note={pulse.id}>
        {pulse.to && <line className="synapse" x1={pulse.to.x} y1={pulse.to.y} x2={pulse.x} y2={pulse.y}/>}
        <circle className="halo" cx={pulse.x} cy={pulse.y} r="14"/>
        <circle className="wave" cx={pulse.x} cy={pulse.y} r="14"/>
      </g>)}
    </svg>}
    {status !== "ready" && <p role="status" className="graph-canvas-state">
      {status === "loading" ? "Arranging graph…" : "Graph visualization unavailable. Use the node list to explore."}
    </p>}
  </div>;
}
