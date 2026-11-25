"""Index freshness: is each derived index built from the repository's current revision?"""

from cerebro.application.ports.graph import GraphIndex
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.search import SearchIndex


def index_is_stale(notes: NoteRepository, search: SearchIndex, graph: GraphIndex) -> bool:
    """``True`` when either index was not built from the repository's current revision (``None`` counts as a
    value: a never-built index of a non-empty repository is stale, and so is an index built from a revision
    of a repository that is now empty). Cheap, so a container can call it at startup and rebuild when it is."""
    head = notes.head()
    return search.built_from() != head or graph.built_from() != head


def advance_revision(search: SearchIndex, graph: GraphIndex, *, previous: str | None, current: str) -> None:
    """After an incremental update that followed the commit ``current``, record it on each index that was fresh
    at ``previous`` (the head read just before the commit). An index that was already stale stays stale, so
    one failed update is never hidden by a later successful one.

    Requires a single writer: reading ``previous`` (``NoteRepository.head()``), ``apply`` and this call must run
    under ONE writer lock held by the composition root (ADR-0002). Without it, a concurrent commit between them
    can make an index look fresh at ``previous`` and be advanced past a commit it never reflected, hiding a
    failed update."""
    if search.built_from() == previous:
        search.set_revision(current)
    if graph.built_from() == previous:
        graph.set_revision(current)
