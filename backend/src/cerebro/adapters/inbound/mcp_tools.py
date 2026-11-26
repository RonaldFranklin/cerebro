"""The authenticated read and write tools. Transport identity comes from the request's authentication callback."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass

from mcp import MCPError, types
from mcp.server import Server, ServerRequestContext

from cerebro.adapters.inbound.input_limits import MAX_FIELD_BYTES, MAX_QUERY_BYTES, bounded_text
from cerebro.adapters.inbound.mcp_write import WRITE_TOOL, write_request
from cerebro.adapters.inbound.read_views import overview_view, read_view, search_view
from cerebro.adapters.inbound.write_guide import read_write_guide
from cerebro.application.agent_write import AgentWriteRequest, AgentWriteResult, WriteFinding, WriteRejected
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
from cerebro.application.errors import (
    AuthenticationFailed,
    NoteNotFound,
    PermissionDenied,
    SectionNotFound,
)
from cerebro.application.use_case import ContextUseCase
from cerebro.domain.access import AccessContext, Scope
from cerebro.domain.enums import Priority, Status
from cerebro.domain.errors import DomainError
from cerebro.domain.note import NoteId
from cerebro.domain.vocabulary import Area, NoteType


@dataclass(frozen=True, slots=True)
class WriteGuideRequest:
    """Fetch the static guidance without reading note content."""


type ToolRequest = (
    GetOverviewRequest
    | SearchNotesRequest
    | ReadNoteRequest
    | GetNeighborsRequest
    | AgentWriteRequest
    | WriteGuideRequest
)


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


_TEXT = {"type": "string", "maxLength": MAX_FIELD_BYTES, "description": f"Up to {MAX_FIELD_BYTES} UTF-8 bytes."}
_TOOLS = {
    tool.name: tool
    for tool in (
        WRITE_TOOL,
        _tool("cerebro_write_guide", "Read the writing rules before creating or updating a note.", {}),
        _tool("cerebro_overview", "Read core notes and visible hubs.", {}),
        _tool(
            "cerebro_search",
            "Search note summaries.",
            {
                "query": {
                    "type": "string",
                    "maxLength": MAX_QUERY_BYTES,
                    "description": f"Up to {MAX_QUERY_BYTES} UTF-8 bytes.",
                },
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
    if tool is None or not isinstance(args, dict):
        raise InvalidToolArguments
    if name == WRITE_TOOL.name:
        return write_request(args)
    if args.keys() - tool.input_schema["properties"].keys():
        raise InvalidToolArguments

    def text(key: str, limit: int = MAX_FIELD_BYTES) -> str:
        val = args.get(key)
        if not isinstance(val, str):
            raise InvalidToolArguments
        return bounded_text(val, limit)

    def integer(key: str, default: int) -> int:
        value = args.get(key, default)
        # JSON Schema treats numbers such as 1.0 as integers. JSON booleans are a separate type.
        if type(value) is int:
            return value
        if type(value) is float and value.is_integer():
            return int(value)
        raise InvalidToolArguments

    try:
        if name == "cerebro_write_guide":
            return WriteGuideRequest()
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
                limit=integer("limit", 5),
            )
        note_id = NoteId(text("id"))
        if name == "cerebro_read":
            return ReadNoteRequest(note_id=note_id, section=text("section") if "section" in args else None)
        depth = integer("depth", 1)
        if not 1 <= depth <= 2:
            raise InvalidToolArguments
        return GetNeighborsRequest(note_id=note_id, hops=depth)
    except DomainError, ValueError:
        pass
    raise InvalidToolArguments


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


def _findings(findings: tuple[WriteFinding, ...]) -> list[dict[str, str]]:
    return [{"code": item.code, "field": item.field, "hint": item.hint} for item in findings]


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(is_error=True, content=[types.TextContent(text=message)])


def tool_scope(name: str) -> Scope:
    return Scope.WRITE if name in {WRITE_TOOL.name, "cerebro_write_guide"} else Scope.READ


@dataclass(frozen=True, slots=True, kw_only=True)
class MCPTools:
    get_overview: ContextUseCase[GetOverviewRequest, Overview]
    search_notes: ContextUseCase[SearchNotesRequest, SearchResults]
    read_note: ContextUseCase[ReadNoteRequest, NoteView]
    get_neighbors: ContextUseCase[GetNeighborsRequest, GraphView]

    agent_write: ContextUseCase[AgentWriteRequest, AgentWriteResult]

    def list_tools(self, ctx: AccessContext) -> types.ListToolsResult:
        if ctx.is_owner:
            raise PermissionDenied("discover tools")
        return types.ListToolsResult(tools=[tool for tool in _TOOLS.values() if ctx.can(tool_scope(tool.name))])

    def call(self, name: str, arguments: object, authenticate: Callable[[], AccessContext]) -> types.CallToolResult:
        try:
            request = parse_tool(name, arguments)
            ctx = authenticate()
            if ctx.is_owner or not ctx.can(tool_scope(name)):
                raise PermissionDenied("call tool")
            output: dict[str, object]
            if isinstance(request, GetOverviewRequest):
                output = dict(overview_view(self.get_overview.execute(request, ctx)))
            elif isinstance(request, SearchNotesRequest):
                output = {"results": search_view(self.search_notes.execute(request, ctx))}
            elif isinstance(request, ReadNoteRequest):
                view = self.read_note.execute(request, ctx)
                output = dict(read_view(view))
                if ctx.can(Scope.WRITE) and view.base_hash is not None:
                    output["base_hash"] = view.base_hash
            elif isinstance(request, AgentWriteRequest):
                receipt = self.agent_write.execute(request, ctx)
                output = {
                    "note_id": receipt.note_id.value,
                    "revision": receipt.revision,
                    "findings": _findings(receipt.findings),
                    "warnings": [{"code": w.code.value, "message": w.message} for w in receipt.warnings],
                }
            elif isinstance(request, WriteGuideRequest):
                output = {"guide": read_write_guide()}
            else:
                output = _neighbors(self.get_neighbors.execute(request, ctx))
            return types.CallToolResult(
                content=[types.TextContent(text=json.dumps(output, ensure_ascii=False))], structured_content=output
            )
        except InvalidToolArguments, DomainError:
            return _error("Invalid tool arguments.")
        except WriteRejected as error:
            details = {"findings": _findings(error.findings)}
            return types.CallToolResult(
                is_error=True, content=[types.TextContent(text=json.dumps(details))], structured_content=details
            )
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
                return self.list_tools(await asyncio.to_thread(authenticate, ctx.request))
            except AuthenticationFailed, PermissionDenied:
                raise MCPError(code=types.INVALID_REQUEST, message="Access denied.") from None
            except Exception:
                raise MCPError(code=types.INTERNAL_ERROR, message="Cérebro offline") from None

        async def call_tool(
            ctx: ServerRequestContext[dict[str, object]], params: types.CallToolRequestParams
        ) -> types.CallToolResult:
            return await asyncio.to_thread(self.call, params.name, params.arguments, lambda: authenticate(ctx.request))

        return Server("cerebro", on_list_tools=list_tools, on_call_tool=call_tool)
