import { expect, test } from "@playwright/test";
import { colorFor } from "../../src/graphAppearance";
import { layout } from "../../src/graphLayout";

test("linked notes cluster in the middle and unlinked notes ring around them", () => {
  const edges: [number, number][] = [];
  for (let i = 1; i < 40; i++) edges.push([i, Math.floor(i / 3)]);
  const positions = layout({count: 52, edges});
  const radius = Array.from({length: 52}, (_, i) => Math.hypot(positions[i * 2], positions[i * 2 + 1]));
  const inner = Math.max(...radius.slice(0, 40));
  expect(positions.every(Number.isFinite)).toBe(true);
  for (const distance of radius.slice(40)) {
    expect(distance).toBeGreaterThan(inner * 1.1);
    expect(distance).toBeLessThan(inner * 1.5);
  }
  expect(layout({count: 52, edges})).toEqual(positions);
});

test("a graph of one note or of unlinked notes still has finite positions", () => {
  expect([...layout({count: 1, edges: []})]).toEqual([0, 0]);
  expect([...layout({count: 0, edges: []})]).toEqual([]);
  expect(layout({count: 3, edges: []}).every(Number.isFinite)).toBe(true);
});

test("named areas keep their theme color and other areas never share one", () => {
  const areas = ["data-science", "development", "infrastructure", "projects", "tools"];
  const colors = areas.map(area => colorFor(area, areas));
  expect(colors[2]).toBe("#f5a524");
  expect(colors[3]).toBe("#5ce1e6");
  expect(new Set(colors).size).toBe(areas.length);
});

test("many unlinked notes spread over a band instead of piling on one circle", () => {
  const count = 300;
  const positions = layout({count, edges: []});
  const points = Array.from({length: count}, (_, i) => [positions[i * 2], positions[i * 2 + 1]]);
  const radius = Math.max(...points.map(([x, y]) => Math.hypot(x, y)));
  let closest = Infinity;
  for (let i = 0; i < count; i++) for (let j = i + 1; j < count; j++) {
    closest = Math.min(closest, Math.hypot(points[i][0] - points[j][0], points[i][1] - points[j][1]));
  }
  // Dots of a graph this size stay about a dot apart.
  expect(closest / radius).toBeGreaterThan(.025);
});
