"""``GetOverview``: the first call of every agent."""

from cerebro.application.access import redacted_body, require_scope
from cerebro.application.dto import CoreNote, GetOverviewRequest, NoteSummary, Overview
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.search import MAX_LIMIT, SearchIndex, SearchQuery
from cerebro.domain.access import AccessContext, Scope, is_visible
from cerebro.domain.budgets import CORE_BUDGET_WORDS, word_count
from cerebro.domain.enums import Priority
from cerebro.domain.vocabulary import HUB


class GetOverview:
    """Current P0 notes within the core budget, plus the hubs (and their areas) the caller may see."""

    def __init__(self, notes: NoteRepository, search: SearchIndex) -> None:
        self._notes = notes
        self._search = search

    def execute(self, request: GetOverviewRequest, ctx: AccessContext) -> Overview:
        require_scope(ctx, Scope.READ)
        core, words, truncated = self._core(ctx)
        hits = self._search.search(SearchQuery(ceiling=ctx.ceiling, types=frozenset({HUB}), limit=MAX_LIMIT))
        hubs = tuple(
            sorted(
                (
                    NoteSummary.from_hit(h)
                    for h in hits
                    if h.type == HUB and is_visible(h.sensitivity, h.status, ctx.ceiling)
                ),
                key=lambda s: (s.area, s.id),
            )
        )
        return Overview(
            core=core,
            core_words=words,
            core_truncated=truncated,
            areas=tuple(sorted({h.area for h in hubs})),
            hubs=hubs,
        )

    def _core(self, ctx: AccessContext) -> tuple[tuple[CoreNote, ...], int, bool]:
        hits = self._search.search(
            SearchQuery(ceiling=ctx.ceiling, priorities=frozenset({Priority.P0}), limit=MAX_LIMIT)
        )
        served: list[CoreNote] = []
        words = 0
        for hit in sorted(hits, key=lambda h: h.id):
            note = self._notes.get(hit.id)
            # The repository is the source of truth: re-check what the (derived) index returned.
            if note is None or not is_visible(note.sensitivity, note.status, ctx.ceiling):
                continue
            if note.priority != Priority.P0:
                continue
            count = word_count(note.body)
            if words + count > CORE_BUDGET_WORDS:
                return tuple(served), words, True
            words += count
            served.append(
                CoreNote(
                    id=note.id, title=note.title, summary=note.summary, body=redacted_body(self._notes, ctx, note.body)
                )
            )
        return tuple(served), words, False
