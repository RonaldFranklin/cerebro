"""``RebuildIndex``: rebuild the derived indexes from the content repository."""

from cerebro.application.access import require_owner
from cerebro.application.dto import RebuildIndexRequest, RebuildIndexResult
from cerebro.application.errors import NoteReadError
from cerebro.application.ports.graph import GraphIndex
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.search import SearchIndex
from cerebro.domain.access import AccessContext
from cerebro.domain.note_ref import NoteRef


class RebuildIndex:
    """Owner only. Replaces the search and graph indexes with what ``NoteRepository`` holds, whatever the caller's
    ceiling (an index must be complete; the ceiling is applied when it is queried).

    Files with a stable problem (``NoteScan``) are left out: the result is still a function of ``head()``, so the
    index is recorded as built from it and one invalid committed file does not take the other notes down. Any
    problem that is not stable (a note file that differs from ``HEAD``, an I/O error) raises ``NoteReadError`` and
    records nothing: no index is ever marked fresh while it could miss or hold uncommitted content.

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
        # Read the head first: a commit landing meanwhile leaves the index looking stale, never fresh.
        revision = self._notes.head()
        scan = self._notes.scan()
        if not scan.stable:
            raise NoteReadError(
                "note files differ from the last commit or cannot be read",
                problems=[(p.path, p.reason) for p in scan.problems if not p.stable],
            )
        self._search.rebuild(scan.notes, revision=revision)
        self._graph.rebuild((NoteRef.from_note(n) for n in scan.notes), revision=revision)
        return RebuildIndexResult(notes_indexed=len(scan.notes), files_excluded=len(scan.problems))
