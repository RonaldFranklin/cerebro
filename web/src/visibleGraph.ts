import { useQuery } from "@tanstack/react-query";
import type { ApiClient, Ceiling } from "./api";

export interface GraphNode {
  id: string; title: string; summary: string; type: string; area: string;
  priority: string; status: string; sensitivity: Ceiling;
}
export interface GraphData {
  nodes: GraphNode[];
  edges: { source: string; target: string; kind: string; section: string | null }[];
  center: string | null;
}

export function useVisibleGraph(api: ApiClient, history: boolean) {
  return useQuery({
    queryKey: ["visible-graph", history],
    queryFn: ({signal}) => api.read<GraphData>("/api/graph?history=" + history, signal),
  });
}
