"""``SearchNotes``: full-text search and filters, summaries only."""

from cerebro.application.access import require_scope
from cerebro.application.dto import NoteSummary, SearchNotesRequest, SearchResult, SearchResults
from cerebro.application.ports.search import SearchIndex, SearchQuery
from cerebro.domain.access import AccessContext, Scope, is_visible


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
        # The index already filters; every hit is re-checked so a faulty adapter cannot leak a hidden note.
        hits = (
            h
            for h in self._search.search(query)
            if is_visible(h.sensitivity, h.status, ctx.ceiling, include_history=request.include_history)
        )
        return SearchResults(results=tuple(SearchResult(note=NoteSummary.from_hit(h), score=h.score) for h in hits))
