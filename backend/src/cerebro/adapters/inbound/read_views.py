"""Allowlisted response schemas and projections shared by the read adapters."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NotRequired, TypedDict

from cerebro.application.dto import (
    HealthCode,
    HealthReportResult,
    HubMember,
    NoteSummary,
    NoteView,
    Overview,
    SearchResults,
    TokenActivityRow,
)
from cerebro.domain.access import AccessCeiling


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


@dataclass(frozen=True, slots=True, kw_only=True)
class ListenerCeilings:
    """One started listener, from configuration only: no origin, address or port."""

    name: str
    mcp: AccessCeiling
    browser: AccessCeiling


class ContentView(TypedDict):
    revision: str | None
    state: str


class ListenerView(TypedDict):
    name: str
    mcp_ceiling: str
    browser_ceiling: str


class LastBackupView(TypedDict):
    state: str
    completed_at: str | None
    content_revision: str | None


class TokenView(TypedDict):
    client: str
    ceiling: str
    scopes: list[str]
    state: str
    usage_known: bool
    last_used: str | None
    activity: list[int]


class BrowserHealthResponse(HealthResponse):
    """The browser also reports when and what was checked, and the configured entrances."""

    checked_at: str
    content: ContentView
    listeners: list[ListenerView]
    last_backup: LastBackupView
    tokens: list[TokenView]


def _token_view(row: TokenActivityRow) -> TokenView:
    return {
        "client": row.agent,
        "ceiling": row.ceiling.level.value,
        "scopes": sorted(scope.value for scope in row.scopes),
        "state": row.state,
        "usage_known": row.usage_known,
        "last_used": row.last_used.isoformat() if row.last_used else None,
        "activity": list(row.hourly),
    }


def browser_health_view(
    report: HealthReportResult, listeners: Sequence[ListenerCeilings], tokens: Sequence[TokenActivityRow]
) -> BrowserHealthResponse:
    file_problems = {HealthCode.NOTE_FILE_EXCLUDED, HealthCode.NOTE_FILE_BLOCKING}
    attention = any(issue.code in file_problems for issue in report.issues)
    return {
        **health_view(report),
        "checked_at": report.checked_at.isoformat(),
        "content": {"revision": report.revision, "state": "attention" if attention else "clean"},
        "last_backup": {
            "state": report.last_backup.state,
            "completed_at": completed.isoformat() if (completed := report.last_backup.completed_at) else None,
            "content_revision": report.last_backup.content_revision,
        },
        "tokens": [_token_view(row) for row in tokens],
        "listeners": [
            {"name": item.name, "mcp_ceiling": item.mcp.level.value, "browser_ceiling": item.browser.level.value}
            for item in listeners
        ],
    }


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
                "subject": issue.subject.replace("\\", "\\\\")
                .encode("utf-8", "surrogateescape")
                .decode("utf-8", "backslashreplace"),
                "code": issue.code.value,
                "severity": issue.severity.value,
                "field": issue.field,
                "message": issue.message,
            }
            for issue in report.issues
        ],
    }
