"""Typed owner reads over the listener-bound context installed by browser authentication."""

from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.responses import JSONResponse

from cerebro.adapters.inbound.input_limits import MAX_FIELD_BYTES, MAX_QUERY_BYTES, bounded_text
from cerebro.adapters.inbound.read_views import (
    GraphResponse,
    HealthResponse,
    NoteResponse,
    OverviewResponse,
    SearchResponse,
    health_view,
    overview_view,
    read_view,
    search_view,
)
from cerebro.application.dto import (
    GetGraphRequest,
    GetNeighborsRequest,
    GetOverviewRequest,
    GraphView,
    HealthReportRequest,
    HealthReportResult,
    NoteView,
    Overview,
    ReadNoteRequest,
    SearchNotesRequest,
    SearchResults,
)
from cerebro.application.errors import NoteNotFound, PermissionDenied, SectionNotFound
from cerebro.application.use_case import ContextUseCase
from cerebro.domain.enums import Priority, Sensitivity, Status
from cerebro.domain.errors import InvalidValueError
from cerebro.domain.note import NoteId
from cerebro.domain.vocabulary import Area, NoteType


@dataclass(frozen=True, slots=True, kw_only=True)
class BrowserReads:
    get_overview: ContextUseCase[GetOverviewRequest, Overview]
    search_notes: ContextUseCase[SearchNotesRequest, SearchResults]
    read_note: ContextUseCase[ReadNoteRequest, NoteView]
    get_neighbors: ContextUseCase[GetNeighborsRequest, GraphView]
    get_graph: ContextUseCase[GetGraphRequest, GraphView]
    health_report: ContextUseCase[HealthReportRequest, HealthReportResult]


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class History(Parameters):
    history: Literal["true", "false"] = "false"


class Filters(History):
    area: str | None = Field(default=None, max_length=MAX_FIELD_BYTES, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    type: str | None = Field(default=None, max_length=MAX_FIELD_BYTES, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    priority: Priority | None = None
    status: Status | None = None


class Search(Filters):
    q: str = ""
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("q")
    @classmethod
    def bound_query(cls, value: str) -> str:
        return bounded_text(value, MAX_QUERY_BYTES) if value else ""


class Neighbors(Filters):
    sensitivity: Sensitivity | None = None
    hops: int = Field(default=1, ge=1, le=2)


class ReadNote(History):
    section: str | None = None

    @field_validator("section")
    @classmethod
    def bound_section(cls, value: str | None) -> str | None:
        return bounded_text(value, MAX_FIELD_BYTES) if value is not None else None


def _neighborhood(graph: GraphView, filters: Neighbors) -> GraphView:
    visible = {
        node.id: node
        for node in graph.nodes
        if (filters.area is None or node.area.value == filters.area)
        and (filters.type is None or node.type.value == filters.type)
        and (filters.priority is None or node.priority is filters.priority)
        and (filters.status is None or node.status is filters.status)
        and (filters.sensitivity is None or node.sensitivity is filters.sensitivity)
    }
    edges = [edge for edge in graph.edges if edge.source in visible and edge.target in visible]
    # Keep local paths within the selected filters, including intermediate nodes on a two-hop path.
    reached = {node_id for node_id in visible if node_id == graph.center}
    for _ in range(filters.hops):
        added = {edge.target for edge in edges if edge.source in reached}
        added.update(edge.source for edge in edges if edge.target in reached)
        reached.update(added)
    return GraphView(
        nodes=tuple(node for node_id, node in visible.items() if node_id in reached),
        edges=tuple(edge for edge in edges if edge.source in reached and edge.target in reached),
        center=graph.center if graph.center in reached else None,
    )


def _graph_view(graph: GraphView) -> GraphResponse:
    return {
        "center": graph.center.value if graph.center else None,
        "nodes": [
            {
                "id": n.id.value,
                "title": n.title,
                "summary": n.summary,
                "type": n.type.value,
                "area": n.area.value,
                "priority": n.priority.value,
                "status": n.status.value,
                "sensitivity": n.sensitivity.value,
            }
            for n in graph.nodes
        ],
        "edges": [
            {"source": e.source.value, "target": e.target.value, "kind": e.kind.value, "section": e.section}
            for e in graph.edges
        ],
    }


def install_browser_reads(app: FastAPI, cases: BrowserReads) -> None:
    async def unique_fields(request: Request) -> None:
        params = request.query_params
        if len(params.multi_items()) != len(params):
            raise InvalidValueError("request", "repeated field")

    router = APIRouter(prefix="/api", dependencies=[Depends(unique_fields)])

    @app.exception_handler(NoteNotFound)
    @app.exception_handler(SectionNotFound)
    async def missing(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"error": "Not found."}, 404)

    @app.exception_handler(PermissionDenied)
    async def denied(request: Request, error: PermissionDenied) -> JSONResponse:
        return JSONResponse({"error": "Access denied."}, 403)

    @router.get("/overview", response_model=OverviewResponse)
    def overview(request: Request, params: Annotated[Parameters, Query()]) -> OverviewResponse:
        result = cases.get_overview.execute(GetOverviewRequest(), request.state.ctx)
        return {**overview_view(result), "core_words": result.core_words}

    @router.get("/search", response_model=SearchResponse)
    def search(request: Request, params: Annotated[Search, Query()]) -> SearchResponse:
        query = SearchNotesRequest(
            text=params.q,
            areas=frozenset([Area(params.area)]) if params.area else frozenset(),
            types=frozenset([NoteType(params.type)]) if params.type else frozenset(),
            priorities=frozenset([params.priority]) if params.priority else frozenset(),
            statuses=frozenset([params.status]) if params.status else frozenset(),
            include_history=params.history == "true",
            limit=params.limit,
        )
        return {"results": search_view(cases.search_notes.execute(query, request.state.ctx))}

    @router.get("/notes/{note_id}", response_model=NoteResponse)
    def note(request: Request, note_id: str, params: Annotated[ReadNote, Query()]) -> NoteResponse:
        query = ReadNoteRequest(
            note_id=NoteId(note_id), section=params.section, include_history=params.history == "true"
        )
        ctx = request.state.ctx
        result = cases.read_note.execute(query, ctx)
        view = read_view(result)
        if (
            params.section is None
            and ctx.ceiling.level is Sensitivity.RESTRICTED
            and result.visible_content_hash == result.content_hash
        ):
            view["content_hash"] = result.content_hash
        return view

    @router.get("/graph")
    def graph(request: Request, params: Annotated[History, Query()]) -> GraphResponse:
        query = GetGraphRequest(include_history=params.history == "true")
        return _graph_view(cases.get_graph.execute(query, request.state.ctx))

    @router.get("/notes/{note_id}/neighbors")
    def neighbors(request: Request, note_id: str, params: Annotated[Neighbors, Query()]) -> GraphResponse:
        query = GetNeighborsRequest(note_id=NoteId(note_id), include_history=params.history == "true", hops=params.hops)
        return _graph_view(_neighborhood(cases.get_neighbors.execute(query, request.state.ctx), params))

    @router.get("/health", response_model=HealthResponse)
    def health(request: Request, params: Annotated[Parameters, Query()]) -> HealthResponse:
        return health_view(cases.health_report.execute(HealthReportRequest(), request.state.ctx))

    app.include_router(router)
