"""Allowlisted JSON response schemas for the browser read API."""

from typing import NotRequired, TypedDict


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


class OverviewResponse(TypedDict):
    core: list[CoreNote]
    core_words: int
    core_truncated: bool
    areas: list[str]
    hubs: list[OverviewHub]


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
