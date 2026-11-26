"""In-memory ``SearchIndex`` and ``GraphIndex``."""

import re
from collections import deque
from collections.abc import Iterable

from cerebro.application.ports.graph import GraphSlice
from cerebro.application.ports.search import SearchHit, SearchQuery
from cerebro.domain.access import AccessCeiling, is_visible
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.links import Link
from cerebro.domain.note import Note, NoteId
from cerebro.domain.note_ref import NoteRef

_WORD = re.compile(r"\w+")


def _terms(text: str) -> frozenset[str]:
    return frozenset(w.casefold() for w in _WORD.findall(text))


def _hit(note: Note, score: float) -> SearchHit:
    return SearchHit(
        id=note.id,
        title=note.title,
        summary=note.summary,
        area=note.area,
        type=note.type,
        priority=note.priority,
        sensitivity=note.sensitivity,
        status=note.status,
        score=score,
    )


class InMemorySearchIndex:
    """Any-term search; a term counts 3 in the title, 2 in the summary and 1 in the body."""

    def __init__(self) -> None:
        self._notes: dict[NoteId, Note] = {}
        self._revision: str | None = None

    def built_from(self) -> str | None:
        return self._revision

    def set_revision(self, revision: str | None) -> None:
        self._revision = revision

    def upsert(self, note: Note) -> None:
        self._notes[note.id] = note

    def remove(self, note_id: NoteId) -> None:
        self._notes.pop(note_id, None)

    def rebuild(self, notes: Iterable[Note], *, revision: str | None = None) -> None:
        self._notes = {n.id: n for n in notes}
        self._revision = revision

    def search(self, query: SearchQuery) -> tuple[SearchHit, ...]:
        terms = _terms(query.text)
        if query.text.strip() and not terms:
            return ()  # text with no searchable word matches nothing; blank text lists
        hits: list[SearchHit] = []
        for note in self._notes.values():
            if not is_visible(note.sensitivity, note.status, query.ceiling, include_history=query.include_history):
                continue
            if query.areas and note.area not in query.areas:
                continue
            if query.types and note.type not in query.types:
                continue
            if query.priorities and note.priority not in query.priorities:
                continue
            if query.statuses and note.status not in query.statuses:
                continue
            score = 0.0
            if terms:
                score = float(
                    sum(3 for t in terms if t in _terms(note.title))
                    + sum(2 for t in terms if t in _terms(note.summary))
                    + sum(1 for t in terms if t in _terms(note.body))
                )
                if score == 0.0:
                    continue
            hits.append(_hit(note, score))
        hits.sort(key=lambda h: (-h.score, h.priority, h.id))
        return tuple(hits[: query.limit])

    def find_similar(self, text: str, *, ceiling: AccessCeiling, limit: int = 5) -> tuple[SearchHit, ...]:
        wanted = _terms(text)
        hits: list[SearchHit] = []
        for note in self._notes.values():
            if not is_visible(note.sensitivity, note.status, ceiling):
                continue
            have = _terms(f"{note.title} {note.summary}")
            union = wanted | have
            score = len(wanted & have) / len(union) if union else 0.0
            if score > 0.0:
                hits.append(_hit(note, score))
        hits.sort(key=lambda h: (-h.score, h.priority, h.id))
        return tuple(hits[:limit])


def _edge_key(link: Link) -> tuple[str, str, str, str]:
    return (link.source.value, link.target.value, link.kind.value, link.section or "")


class InMemoryGraphIndex:
    def __init__(self) -> None:
        self._refs: dict[NoteId, NoteRef] = {}
        self._revision: str | None = None

    def built_from(self) -> str | None:
        return self._revision

    def set_revision(self, revision: str | None) -> None:
        self._revision = revision

    def upsert(self, ref: NoteRef) -> None:
        self._refs[ref.id] = ref

    def remove(self, note_id: NoteId) -> None:
        self._refs.pop(note_id, None)

    def rebuild(self, refs: Iterable[NoteRef], *, revision: str | None = None) -> None:
        self._refs = {r.id: r for r in refs}
        self._revision = revision

    def _visible(self, ceiling: AccessCeiling, include_history: bool) -> dict[NoteId, NoteRef]:
        return {
            i: r
            for i, r in self._refs.items()
            if is_visible(r.sensitivity, r.status, ceiling, include_history=include_history)
        }

    @staticmethod
    def _edges(visible: dict[NoteId, NoteRef]) -> list[Link]:
        links = [link for ref in visible.values() for link in ref.links if link.target in visible]
        return sorted(links, key=_edge_key)

    @staticmethod
    def _slice(visible: dict[NoteId, NoteRef], ids: set[NoteId]) -> GraphSlice:
        edges = [e for e in InMemoryGraphIndex._edges(visible) if e.source in ids and e.target in ids]
        return GraphSlice(nodes=tuple(visible[i] for i in sorted(ids)), edges=tuple(edges))

    def graph(self, *, ceiling: AccessCeiling, include_history: bool = False) -> GraphSlice:
        visible = self._visible(ceiling, include_history)
        return self._slice(visible, set(visible))

    def backlinks(self, note_id: NoteId, *, ceiling: AccessCeiling, include_history: bool = False) -> tuple[Link, ...]:
        visible = self._visible(ceiling, include_history)
        if note_id not in visible:
            return ()
        return tuple(e for e in self._edges(visible) if e.target == note_id)

    def neighbors(
        self, note_id: NoteId, *, hops: int, ceiling: AccessCeiling, include_history: bool = False
    ) -> GraphSlice:
        if type(hops) is not int or hops < 1:
            raise InvalidValueError("hops", "expected an integer >= 1")
        visible = self._visible(ceiling, include_history)
        if note_id not in visible:
            return GraphSlice(nodes=(), edges=())
        adjacent: dict[NoteId, set[NoteId]] = {i: set() for i in visible}
        for e in self._edges(visible):
            adjacent[e.source].add(e.target)
            adjacent[e.target].add(e.source)
        seen = {note_id}
        queue = deque([(note_id, 0)])
        while queue:
            current, depth = queue.popleft()
            if depth == hops:
                continue
            for nxt in adjacent[current] - seen:
                seen.add(nxt)
                queue.append((nxt, depth + 1))
        return self._slice(visible, seen)

    def orphans(self, *, ceiling: AccessCeiling, include_history: bool = False) -> tuple[NoteId, ...]:
        visible = self._visible(ceiling, include_history)
        connected = {end for e in self._edges(visible) for end in (e.source, e.target)}
        return tuple(sorted(set(visible) - connected))

    def broken_links(self, *, ceiling: AccessCeiling, include_history: bool = False) -> tuple[Link, ...]:
        visible = self._visible(ceiling, include_history)
        links = [link for ref in visible.values() for link in ref.links if link.target not in self._refs]
        return tuple(sorted(links, key=_edge_key))
