"""JSON views for deliberate output to the local owner."""

from cerebro.adapters.inbound.read_views import read_view
from cerebro.application.dto import NoteView


def note_view(note: NoteView) -> dict[str, object]:
    return {**read_view(note), "content_hash": note.content_hash}
