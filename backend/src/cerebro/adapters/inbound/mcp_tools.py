"""The four read tools. Transport identity comes from the request's authentication callback."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass

from mcp import MCPError, types
from mcp.server import Server, ServerRequestContext

from cerebro.adapters.inbound.input_limits import MAX_FIELD_BYTES, MAX_QUERY_BYTES, bounded_text
from cerebro.adapters.inbound.read_views import read_view, search_view
from cerebro.application.dto import (
    GetNeighborsRequest,
    GetOverviewRequest,
    GraphView,
    NoteView,
    Overview,
    ReadNoteRequest,
    SearchNotesRequest,
    SearchResults,
)
from cerebro.application.errors import AuthenticationFailed, NoteNotFound, PermissionDenied, SectionNotFound
from cerebro.application.use_case import ContextUseCase
from cerebro.domain.access import AccessContext, Scope
from cerebro.domain.enums import Priority, Status
from cerebro.domain.errors import DomainError
from cerebro.domain.note import NoteId
from cerebro.domain.vocabulary import Area, NoteType

type ToolRequest = GetOverviewRequest | SearchNotesRequest | ReadNoteRequest | GetNeighborsRequest


class InvalidToolArguments(ValueError):  # noqa: N818
    """A fixed diagnostic used also by transport preflight."""

    def __init__(self) -> None:
        super().__init__("Invalid tool arguments.")


def _tool(name: str, description: str, properties: dict[str, object], *required: str) -> types.Tool:
    return types.Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
    )


_TEXT = {"type": "string", "maxLength": MAX_FIELD_BYTES}
_TOOLS = {
    tool.name: tool
    for tool in (
        _tool("cerebro_overview", "Read core notes and visible hubs.", {}),
        _tool(
            "cerebro_search",
            "Search note summaries.",
            {
                "query": {"type": "string", "maxLength": MAX_QUERY_BYTES},
                "area": _TEXT,
                "type": _TEXT,
                "priority": {"type": "string", "enum": [p.value for p in Priority]},
                "status": {"type": "string", "enum": [s.value for s in Status]},
                "include_history": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 5},
            },
            "query",
        ),
        _tool("cerebro_read", "Read one note or section.", {"id": _TEXT, "section": _TEXT}, "id"),
        _tool(
            "cerebro_neighbors",
            "Read linked note summaries.",
            {
                "id": _TEXT,
                "depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2,
                    "default": 1,
                },
            },
            "id",
        ),
    )
}


def parse_tool(name: str, arguments: object) -> ToolRequest:
    """Validate field sizes and types before invoking authentication or a read use case."""
    args = {} if arguments is None else arguments
    tool = _TOOLS.get(name)
    if tool is None or not isinstance(args, dict) or args.keys() - tool.input_schema["properties"].keys():
        raise InvalidToolArguments

    def text(key: str, limit: int = MAX_FIELD_BYTES) -> str:
        val = args.get(key)
        if not isinstance(val, str):
            raise InvalidToolArguments
        return bounded_text(val, limit)

    try:
        if name == "cerebro_overview":
            return GetOverviewRequest()
        if name == "cerebro_search":
            return SearchNotesRequest(
                text=text("query", MAX_QUERY_BYTES),
                areas=frozenset([Area(text("area"))]) if "area" in args else frozenset(),
                types=frozenset([NoteType(text("type"))]) if "type" in args else frozenset(),
                priorities=frozenset([Priority(text("priority"))]) if "priority" in args else frozenset(),
                statuses=frozenset([Status(text("status"))]) if "status" in args else frozenset(),
                include_history=args.get("include_history", False),
                limit=args.get("limit", 5),
            )
        note_id = NoteId(text("id"))
        if name == "cerebro_read":
            return ReadNoteRequest(note_id=note_id, section=text("section") if "section" in args else None)
        depth = args.get("depth", 1)
        if type(depth) is not int or not 1 <= depth <= 2:
            raise InvalidToolArguments
        return GetNeighborsRequest(note_id=note_id, hops=depth)
    except DomainError, ValueError:
        pass
    raise InvalidToolArguments


def _overview(result: Overview) -> dict[str, object]:
    return {
        "core": [{"id": n.id.value, "title": n.title, "summary": n.summary, "body": n.body} for n in result.core],
        "core_truncated": result.core_truncated,
        "areas": [a.value for a in result.areas],
        "hubs": [{"id": n.id.value, "title": n.title, "summary": n.summary} for n in result.hubs],
    }


def _neighbors(result: GraphView) -> dict[str, object]:
    relations: dict[NoteId, list[dict[str, str]]] = {node.id: [] for node in result.nodes}
    for edge in result.edges:
        relation = {"source": edge.source.value, "target": edge.target.value, "kind": edge.kind.value}
        if edge.section is not None:
            relation["section"] = edge.section
        relations[edge.source].append(relation)
        if edge.target != edge.source:
            relations[edge.target].append(relation)
    return {
        "neighbors": [
            {"id": node.id.value, "title": node.title, "summary": node.summary, "relation": relations[node.id]}
            for node in result.nodes
            if node.id != result.center
        ]
    }


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(is_error=True, content=[types.TextContent(text=message)])


def _reader(ctx: AccessContext) -> AccessContext:
    if ctx.is_owner or not ctx.can(Scope.READ):
        raise PermissionDenied("read")
    return ctx


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadTools:
    get_overview: ContextUseCase[GetOverviewRequest, Overview]
    search_notes: ContextUseCase[SearchNotesRequest, SearchResults]
    read_note: ContextUseCase[ReadNoteRequest, NoteView]
    get_neighbors: ContextUseCase[GetNeighborsRequest, GraphView]

    def list_tools(self) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(_TOOLS.values()))

    def call(self, name: str, arguments: object, authenticate: Callable[[], AccessContext]) -> types.CallToolResult:
        try:
            request = parse_tool(name, arguments)
            ctx = _reader(authenticate())
            if isinstance(request, GetOverviewRequest):
                output = _overview(self.get_overview.execute(request, ctx))
            elif isinstance(request, SearchNotesRequest):
                output = {"results": search_view(self.search_notes.execute(request, ctx))}
            elif isinstance(request, ReadNoteRequest):
                output = read_view(self.read_note.execute(request, ctx))
            else:
                output = _neighbors(self.get_neighbors.execute(request, ctx))
            return types.CallToolResult(
                content=[types.TextContent(text=json.dumps(output, ensure_ascii=False))], structured_content=output
            )
        except InvalidToolArguments:
            return _error("Invalid tool arguments.")
        except NoteNotFound, SectionNotFound:
            return _error("Not found.")
        except PermissionDenied, AuthenticationFailed:
            return _error("Access denied.")
        except Exception:
            # Filesystem, SQLite or SDK errors may contain private content. The wire error is fixed.
            return _error("Cérebro offline")

    def server(self, authenticate: Callable[[object], AccessContext]) -> Server[dict[str, object]]:
        async def list_tools(
            ctx: ServerRequestContext[dict[str, object]], params: types.PaginatedRequestParams | None
        ) -> types.ListToolsResult:
            try:
                _reader(await asyncio.to_thread(authenticate, ctx.request))
            except AuthenticationFailed, PermissionDenied:
                raise MCPError(code=types.INVALID_REQUEST, message="Access denied.") from None
            except Exception:
                raise MCPError(code=types.INTERNAL_ERROR, message="Cérebro offline") from None
            return self.list_tools()

        async def call_tool(
            ctx: ServerRequestContext[dict[str, object]], params: types.CallToolRequestParams
        ) -> types.CallToolResult:
            return await asyncio.to_thread(self.call, params.name, params.arguments, lambda: authenticate(ctx.request))

        return Server("cerebro", on_list_tools=list_tools, on_call_tool=call_tool)
