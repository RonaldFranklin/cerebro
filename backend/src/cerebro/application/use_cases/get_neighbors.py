"""``GetNeighbors``: links around one note, up to N hops."""

from cerebro.application.access import require_scope, visible_note
from cerebro.application.dto import GetNeighborsRequest, GraphView
from cerebro.application.ports.graph import GraphIndex
from cerebro.application.ports.notes import NoteRepository
from cerebro.domain.access import AccessContext, Scope


class GetNeighbors:
    def __init__(self, notes: NoteRepository, graph: GraphIndex) -> None:
        self._notes = notes
        self._graph = graph

    def execute(self, request: GetNeighborsRequest, ctx: AccessContext) -> GraphView:
        require_scope(ctx, Scope.READ)
        # Hidden and missing centers fail the same way, before the graph is asked anything.
        visible_note(self._notes, request.note_id, ctx, include_history=request.include_history)
        graph = self._graph.neighbors(
            request.note_id, hops=request.hops, ceiling=ctx.ceiling, include_history=request.include_history
        )
        return GraphView.from_slice(
            graph, ceiling=ctx.ceiling, include_history=request.include_history, center=request.note_id
        )
