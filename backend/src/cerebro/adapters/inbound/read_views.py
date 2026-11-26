"""Allowlisted response schemas and projections shared by the read adapters."""

from typing import NotRequired, TypedDict

from cerebro.application.dto import HealthReportResult, HubMember, NoteSummary, NoteView, Overview, SearchResults


class Summary(TypedDict):
    id: str
    title: str
    type: str
    priority: str
    status: str
    summary: str


class SearchHit(Summary):
    area: str


class SearchResponse(TypedDict):
    results: list[SearchHit]


class CoreNote(TypedDict):
    id: str
    title: str
    summary: str
    body: str


class OverviewHub(TypedDict):
    id: str
    title: str
    summary: str


class OverviewView(TypedDict):
    core: list[CoreNote]
    core_truncated: bool
    areas: list[str]
    hubs: list[OverviewHub]


class OverviewResponse(OverviewView):
    """The browser also reports the size of the core."""

    core_words: int


class NoteResponse(SearchHit):
    sensitivity: str
    hub: str | None
    related: list[str]
    superseded_by: str | None
    lang: str
    created: str
    updated: str
    review_by: str | None
    section: str | None
    body: str
    members: list[Summary] | None
    content_hash: NotRequired[str]


class GraphNode(SearchHit):
    sensitivity: str


class GraphEdge(TypedDict):
    source: str
    target: str
    kind: str
    section: str | None


class GraphResponse(TypedDict):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    center: str | None


class HealthIssue(TypedDict):
    subject: str
    code: str
    severity: str
    field: str | None
    message: str


class HealthResponse(TypedDict):
    ok: bool
    notes_checked: int
    search_index_checked: bool
    issues: list[HealthIssue]


def _summary(note: NoteSummary | HubMember) -> Summary:
    return {
        "id": note.id.value,
        "title": note.title,
        "type": note.type.value,
        "priority": note.priority.value,
        "status": note.status.value,
        "summary": note.summary,
    }


def search_view(result: SearchResults) -> list[SearchHit]:
    return [{**_summary(hit.note), "area": hit.note.area.value} for hit in result.results]


def read_view(note: NoteView) -> NoteResponse:
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


def overview_view(result: Overview) -> OverviewView:
    return {
        "core": [{"id": n.id.value, "title": n.title, "summary": n.summary, "body": n.body} for n in result.core],
        "core_truncated": result.core_truncated,
        "areas": [a.value for a in result.areas],
        "hubs": [{"id": n.id.value, "title": n.title, "summary": n.summary} for n in result.hubs],
    }


def health_view(report: HealthReportResult) -> HealthResponse:
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
