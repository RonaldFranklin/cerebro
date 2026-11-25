"""JSON views for deliberate output to the local owner."""

from cerebro.adapters.inbound.read_views import read_view
from cerebro.application.dto import HealthReportResult, NoteView


def note_view(note: NoteView) -> dict[str, object]:
    return {**read_view(note), "content_hash": note.content_hash}


def health_view(report: HealthReportResult) -> dict[str, object]:
    return {
        "ok": report.ok,
        "notes_checked": report.notes_checked,
        "proposals_checked": report.proposals_checked,
        "search_index_checked": report.search_index_checked,
        "issues": [
            {
                "subject": issue.subject,
                "code": issue.code.value,
                "severity": issue.severity.value,
                "field": issue.field,
                "message": issue.message,
            }
            for issue in report.issues
        ],
    }
