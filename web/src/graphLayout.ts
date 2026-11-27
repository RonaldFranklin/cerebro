import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";

export interface LayoutInput { count: number; edges: [number, number][] }

const goldenAngle = Math.PI * (3 - Math.sqrt(5));

/**
 * A free, round layout: ForceAtlas2 pulls linked notes into clusters around one center, and notes without links
 * drift on a loose ring around them. Returns ``x, y`` pairs by node index. Deterministic for the same input.
 */
export function layout({count, edges}: LayoutInput): Float32Array {
  const linked = new Set(edges.flat());
  const graph = new Graph({multi: true});
  let placed = 0;
  for (let i = 0; i < count; i++) {
    if (!linked.has(i)) continue;
    const radius = Math.sqrt(placed);
    graph.addNode(i, {x: radius * Math.cos(placed * goldenAngle), y: radius * Math.sin(placed * goldenAngle)});
    placed++;
  }
  for (const [a, b] of edges) graph.addEdge(a, b);
  if (graph.order > 1) {
    forceAtlas2.assign(graph, {
      iterations: graph.order < 500 ? 200 : 60,
      settings: {...forceAtlas2.inferSettings(graph), strongGravityMode: true, gravity: .6, scalingRatio: 8,
        barnesHutOptimize: graph.order >= 200},
    });
  }
  const positions = new Float32Array(count * 2);
  let centerX = 0, centerY = 0;
  graph.forEachNode((_key, node) => { centerX += node.x / graph.order; centerY += node.y / graph.order; });
  let radius = 0;
  graph.forEachNode((key, node) => {
    const x = node.x - centerX, y = node.y - centerY;
    radius = Math.max(radius, Math.hypot(x, y));
    positions.set([x, y], Number(key) * 2);
  });
  const ring = Math.max(radius, 10) * 1.3;
  let loose = 0;
  const alone = count - graph.order;
  // More loose notes need more room: they take turns on concentric rings of a widening band.
  const rings = Math.ceil(alone / 60);
  for (let i = 0; i < count; i++) {
    if (linked.has(i)) continue;
    // Evenly spread, with a small repeatable wobble so the ring does not look drawn.
    const angle = loose / alone * 2 * Math.PI + .35;
    const wobble = (rings > 1 ? .03 : .09) * Math.sin(loose * goldenAngle * 3);
    const distance = count === 1 ? 0 : ring * (1 + (loose % rings) * .13 + wobble);
    positions.set([distance * Math.cos(angle), distance * Math.sin(angle)], i * 2);
    loose++;
  }
  return positions;
}
