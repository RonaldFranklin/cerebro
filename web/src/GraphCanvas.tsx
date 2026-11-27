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
  onSelect: (id: string) => void;
}

/** Names are drawn for hubs and the selected note only; hubs carry a white ring. */
function emphasis(hub: boolean, selected: boolean) {
  return {
    labelVisibility: hub || selected ? "visible" : "hidden",
    ringVisibility: hub || selected ? "visible" : "hidden",
    ringColor: selected ? "#5ce1e6" : "#e6edf5",
    // The selection also differs in shape, not only in color: a wider ring further out.
    ringWidth: selected ? 3 : 2,
    ringPadding: selected ? 5 : 2,
  };
}

export function GraphCanvas({data, colorBy, colorOf, selected, onSelect}: Props) {
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
    const graph = renderer.current?.getGraph();
    if (graph) graph.updateEachNodeAttributes((_key, attrs) => ({...attrs, color: colorOf(attrs[colorBy]),
      ...emphasis(attrs.type === "hub", attrs.id === selected)}));
  }, [colorBy, colorOf, selected]);

  useLayoutEffect(() => {
    const host = surface.current;
    if (!host) return;
    let active = true;
    let worker: Worker | undefined;
    let sigma: Sigma | undefined;
    let resize: ResizeObserver | undefined;
    function dispose() {
      worker?.terminate();
      resize?.disconnect();
      sigma?.kill();
      sigma = undefined;
      renderer.current = null;
      host?.replaceChildren();
    }
    function fallback() {
      if (!active) return;
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
          if (!active) return;
          if (positions.length !== data.nodes.length * 2 || !positions.every(Number.isFinite)) { fallback(); return; }
          const graph = new Graph({multi:true});
          data.nodes.forEach((node, i) => graph.addNode(i, {
            x:positions[i * 2], y:positions[i * 2 + 1], label:node.title, area:node.area, type:node.type, id:node.id,
            color:color.current.colorOf(node[color.current.colorBy]), size:sizeFor(node.priority),
            ...emphasis(node.type === "hub", node.id === chosen.current),
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
          sigma.on("webglContextLost", () => { if (active) setStatus("fallback"); });
          sigma.on("webglContextRestored", () => { if (active) setStatus("ready"); });
          sigma.on("clickNode", ({node}) => select.current(data.nodes[Number(node)].id));
          resize = new ResizeObserver(() => { if (active) sigma?.resize(); });
          resize.observe(host);
          setStatus("ready");
        } catch { fallback(); }
      };
      worker.postMessage({count:data.nodes.length, edges} satisfies LayoutInput);
    } catch { fallback(); }
    return () => { active = false; dispose(); };
  }, [data]);

  return <div className="graph-canvas" data-renderer={status}>
    <div className="sigma-surface" ref={surface} aria-hidden="true"/>
    {status !== "ready" && <p role="status" className="graph-canvas-state">
      {status === "loading" ? "Arranging graph…" : "Graph visualization unavailable. Use the node list to explore."}
    </p>}
  </div>;
}
