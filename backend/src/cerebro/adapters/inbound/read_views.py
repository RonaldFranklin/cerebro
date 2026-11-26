"""Allowlisted response fields shared by the read adapters."""

from cerebro.application.dto import HealthReportResult, HubMember, NoteSummary, NoteView, Overview, SearchResults


def _summary(note: NoteSummary | HubMember) -> dict[str, object]:
    return {
        "id": note.id.value,
        "title": note.title,
        "type": note.type.value,
        "priority": note.priority.value,
        "status": note.status.value,
        "summary": note.summary,
    }


def search_view(result: SearchResults) -> list[dict[str, object]]:
    return [{**_summary(hit.note), "area": hit.note.area.value} for hit in result.results]


def read_view(note: NoteView) -> dict[str, object]:
    return {
        "id": note.id.value,
        "title": note.title,
        "type": note.type.value,
        "area": note.area.value,
        "priority": note.priority.value,
        "sensitivity": note.sensitivity.value,
        "status": note.status.value,
        "summary": note.summary,
        "hub": note.hub.value if note.hub else None,
        "related": [item.value for item in note.related],
        "superseded_by": note.superseded_by.value if note.superseded_by else None,
        "lang": note.lang,
        "created": note.created.isoformat(),
        "updated": note.updated.isoformat(),
        "review_by": note.review_by.isoformat() if note.review_by else None,
        "section": note.section,
        "body": note.body,
        "members": [_summary(member) for member in note.members] if note.members is not None else None,
    }


def overview_view(result: Overview) -> dict[str, object]:
    return {
        "core": [{"id": n.id.value, "title": n.title, "summary": n.summary, "body": n.body} for n in result.core],
        "core_truncated": result.core_truncated,
        "areas": [a.value for a in result.areas],
        "hubs": [{"id": n.id.value, "title": n.title, "summary": n.summary} for n in result.hubs],
    }


def health_view(report: HealthReportResult) -> dict[str, object]:
    return {
        "ok": report.ok,
        "notes_checked": report.notes_checked,
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
