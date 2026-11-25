"""``RebuildIndex``: rebuild the derived indexes from the content repository."""

from cerebro.application.access import require_owner
from cerebro.application.dto import RebuildIndexRequest, RebuildIndexResult
from cerebro.application.ports.graph import GraphIndex
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.search import SearchIndex
from cerebro.domain.access import AccessContext
from cerebro.domain.note_ref import NoteRef


class RebuildIndex:
    """Owner only. Replaces the search and graph indexes with what ``NoteRepository`` holds, whatever the caller's
    ceiling (an index must be complete; the ceiling is applied when it is queried).

    This is also the recovery path when an index update failed after a commit: the repository is the source of
    truth, so running it again is always safe (idempotent). Each ``rebuild`` is atomic; if the second one fails
    the error propagates and the use case can simply be run again.
    """

    def __init__(self, notes: NoteRepository, search: SearchIndex, graph: GraphIndex) -> None:
        self._notes = notes
        self._search = search
        self._graph = graph

    def execute(self, request: RebuildIndexRequest, ctx: AccessContext) -> RebuildIndexResult:
        require_owner(ctx, "rebuild the index")
        notes = self._notes.list_notes()
        self._search.rebuild(notes)
        self._graph.rebuild(NoteRef.from_note(n) for n in notes)
        return RebuildIndexResult(notes_indexed=len(notes))
