"""``NoteRef``: a light, body-free view of a note, the element of the cross-note validation snapshot."""

from dataclasses import dataclass

from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.links import Link, WikiLink, extract_wikilinks, links_from_references, loose_link_targets
from cerebro.domain.note import Note, NoteId
from cerebro.domain.vocabulary import HUB, NoteType


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteRef:
    """Metadata and outgoing references of one note, without its body.

    ``body_links`` are the wikilinks already extracted from the body, so cross-note rules never need it.
    """

    id: NoteId
    title: str
    summary: str
    type: NoteType
    priority: Priority
    sensitivity: Sensitivity
    status: Status
    hub: NoteId | None
    related: tuple[NoteId, ...]
    superseded_by: NoteId | None
    body_links: tuple[WikiLink, ...]
    loose_body_targets: tuple[NoteId, ...]
    """Every id in a ``[[...]]`` of the body, including code and alias forms (what read-time redaction covers)."""

    @classmethod
    def from_note(cls, note: Note) -> NoteRef:
        return cls(
            id=note.id,
            title=note.title,
            summary=note.summary,
            type=note.type,
            priority=note.priority,
            sensitivity=note.sensitivity,
            status=note.status,
            hub=note.hub,
            related=note.related,
            superseded_by=note.superseded_by,
            body_links=extract_wikilinks(note.body),
            loose_body_targets=loose_link_targets(note.body),
        )

    @property
    def is_hub(self) -> bool:
        return self.type == HUB

    @property
    def links(self) -> tuple[Link, ...]:
        return links_from_references(
            source=self.id,
            body_links=self.body_links,
            hub=self.hub,
            related=self.related,
            superseded_by=self.superseded_by,
        )
