"""``SearchNotes``: full-text search and filters, summaries only."""

from cerebro.application.access import require_scope
from cerebro.application.dto import NoteSummary, SearchNotesRequest, SearchResult, SearchResults
from cerebro.application.ports.search import SearchIndex, SearchQuery
from cerebro.domain.access import AccessContext, Scope


class SearchNotes:
    def __init__(self, search: SearchIndex) -> None:
        self._search = search

    def execute(self, request: SearchNotesRequest, ctx: AccessContext) -> SearchResults:
        require_scope(ctx, Scope.READ)
        query = SearchQuery(
            ceiling=ctx.ceiling,
            text=request.text,
            areas=request.areas,
            types=request.types,
            priorities=request.priorities,
            statuses=request.statuses,
            include_history=request.include_history,
            limit=request.limit,
        )
        hits = self._search.search(query)
        return SearchResults(results=tuple(SearchResult(note=NoteSummary.from_hit(h), score=h.score) for h in hits))
