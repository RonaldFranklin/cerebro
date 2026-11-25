"""Link extraction: wikilinks in the body plus the ``hub``, ``related`` and ``superseded_by`` references."""

import re
from dataclasses import dataclass
from enum import StrEnum

from cerebro.domain.markdown import strip_code
from cerebro.domain.note import Note, NoteId
from cerebro.domain.slug import is_slug

# [[note-id]] or [[note-id#Section heading]]; no nesting, no line breaks inside the brackets.
_WIKILINK = re.compile(r"\[\[([^\[\]#|\n]*)(?:#([^\[\]|\n]*))?\]\]")


class LinkKind(StrEnum):
    BODY = "body"
    HUB = "hub"
    RELATED = "related"
    SUPERSEDED_BY = "superseded_by"


@dataclass(frozen=True, slots=True, kw_only=True)
class WikiLink:
    """A ``[[id]]`` / ``[[id#Section]]`` link found in a body."""

    target: NoteId
    section: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Link:
    """A directed edge of the knowledge graph."""

    source: NoteId
    target: NoteId
    kind: LinkKind
    section: str | None = None


def extract_wikilinks(body: str) -> tuple[WikiLink, ...]:
    """Wikilinks of ``body`` in order of appearance, without duplicates.

    Links inside fenced code blocks and inline code are examples, not links, and are ignored, as are
    targets that are not valid note ids (they cannot reference a note). A link to the same note with a
    different section is kept as a distinct link.
    """
    found: dict[WikiLink, None] = {}
    for match in _WIKILINK.finditer(strip_code(body)):
        target = match.group(1).strip()
        if not is_slug(target):
            continue
        section = (match.group(2) or "").strip() or None
        found[WikiLink(target=NoteId(target), section=section)] = None
    return tuple(found)


def links_from_references(
    *,
    source: NoteId,
    body_links: tuple[WikiLink, ...],
    hub: NoteId | None,
    related: tuple[NoteId, ...],
    superseded_by: NoteId | None,
) -> tuple[Link, ...]:
    """Every outgoing edge of a note: body links, then ``hub``, ``related`` and ``superseded_by``."""
    links = [Link(source=source, target=b.target, kind=LinkKind.BODY, section=b.section) for b in body_links]
    if hub is not None:
        links.append(Link(source=source, target=hub, kind=LinkKind.HUB))
    links.extend(Link(source=source, target=r, kind=LinkKind.RELATED) for r in related)
    if superseded_by is not None:
        links.append(Link(source=source, target=superseded_by, kind=LinkKind.SUPERSEDED_BY))
    return tuple(links)


def note_links(note: Note) -> tuple[Link, ...]:
    """Every outgoing edge of ``note``."""
    return links_from_references(
        source=note.id,
        body_links=extract_wikilinks(note.body),
        hub=note.hub,
        related=note.related,
        superseded_by=note.superseded_by,
    )
