"""Index freshness: is each derived index built from the repository's current revision?"""

from cerebro.application.ports.graph import GraphIndex
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.search import SearchIndex
from cerebro.domain.note import Note
from cerebro.domain.note_ref import NoteRef


def index_is_stale(notes: NoteRepository, search: SearchIndex, graph: GraphIndex) -> bool:
    """``True`` when either index was not built from the repository's current revision (``None`` counts as a
    value: a never-built index of a non-empty repository is stale, and so is an index built from a revision
    of a repository that is now empty). Cheap: the composition root calls it before every index read."""
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


def index_commit(search: SearchIndex, graph: GraphIndex, note: Note, *, previous: str | None, revision: str) -> None:
    """Reflect ``note``, committed as ``revision`` on top of ``previous``, in both indexes, then advance them.

    The commit is durable, so a failure is not reported to the writer: it leaves at least one recorded revision
    behind ``head()``, and the composition root rebuilds before any later index read (or the read fails). The
    revision always moves last, so no failure can mark an index fresh while it still holds a wider projection."""
    try:
        search.upsert(note)
        graph.upsert(NoteRef.from_note(note))
        advance_revision(search, graph, previous=previous, current=revision)
    except Exception:  # any index failure is recovered by the stale-index rebuild
        pass
