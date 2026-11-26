import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";

export interface LayoutInput { count: number; edges: [number, number][] }

// Only topology and numeric indexes cross this boundary. Node text stays on the UI thread.
self.onmessage = ({data}: MessageEvent<LayoutInput>) => {
  const graph = new Graph({ multi: true });
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < data.count; i++) {
    const radius = Math.sqrt(i);
    graph.addNode(i, {x: radius * Math.cos(i * goldenAngle), y: radius * Math.sin(i * goldenAngle)});
  }
  for (const [a, b] of data.edges) graph.addEdge(a, b);
  if (graph.order > 1) {
    forceAtlas2.assign(graph, {
      iterations: graph.order < 500 ? 100 : 45,
      settings: { ...forceAtlas2.inferSettings(graph), barnesHutOptimize: graph.order >= 200 },
    });
  }
  const positions = new Float32Array(data.count * 2);
  for (let i = 0; i < data.count; i++) {
    positions[i * 2] = graph.getNodeAttribute(i, "x");
    positions[i * 2 + 1] = graph.getNodeAttribute(i, "y");
  }
  self.postMessage(positions, { transfer: [positions.buffer] });
};
