"""``GetGraph``: the whole graph the caller may see (feeds the web UI)."""

from cerebro.application.access import require_scope
from cerebro.application.dto import GetGraphRequest, GraphView
from cerebro.application.ports.graph import GraphIndex
from cerebro.domain.access import AccessContext, Scope


class GetGraph:
    def __init__(self, graph: GraphIndex) -> None:
        self._graph = graph

    def execute(self, request: GetGraphRequest, ctx: AccessContext) -> GraphView:
        require_scope(ctx, Scope.READ)
        graph = self._graph.graph(ceiling=ctx.ceiling, include_history=request.include_history)
        return GraphView.from_slice(graph, ceiling=ctx.ceiling, include_history=request.include_history)
