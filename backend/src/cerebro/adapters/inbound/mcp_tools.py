"""The authenticated read and write tools. Transport identity comes from the request's authentication callback."""

import asyncio
import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

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
    GetVocabularyRequest,
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
from cerebro.domain.note import MAX_NOTE_ID_LENGTH, NoteId
from cerebro.domain.vocabulary import Area, NoteType, Vocabulary

type ToolRequest = (
    GetOverviewRequest
    | SearchNotesRequest
    | ReadNoteRequest
    | GetNeighborsRequest
    | AgentWriteRequest
    | GetVocabularyRequest
)


class InvalidToolArguments(ValueError):  # noqa: N818
    """A diagnostic used also by transport preflight. ``problem`` names the field and what it accepts; it is built
    from the tool schema only, never from a received value or key."""

    def __init__(self, problem: str) -> None:
        super().__init__(f"Invalid tool arguments: {problem}.")


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
        _tool("cerebro_write_guide", "Read the writing rules, areas and types before writing a note.", {}),
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
    if tool is None:
        raise InvalidToolArguments("unknown tool")
    if not isinstance(args, dict):
        raise InvalidToolArguments("arguments must be an object")
    if name == WRITE_TOOL.name:
        return write_request(args)
    properties = tool.input_schema["properties"]
    if args.keys() - properties.keys():
        accepted = ", ".join(properties) or "no arguments"
        raise InvalidToolArguments(f"unknown argument; {name} accepts {accepted}")

    def text(key: str, expected: str = "a string", limit: int = MAX_FIELD_BYTES) -> str:
        value = args.get(key)
        if isinstance(value, str):
            with contextlib.suppress(ValueError):  # too long, or not encodable as UTF-8
                return bounded_text(value, limit)
        raise InvalidToolArguments(f"{key} must be {expected} of at most {limit} UTF-8 bytes")

    def kebab[T](key: str, build: Callable[[str], T]) -> frozenset[T]:
        if key not in args:
            return frozenset()
        expected = "a kebab-case string"
        value = text(key, expected)
        with contextlib.suppress(DomainError):
            return frozenset([build(value)])
        raise InvalidToolArguments(f"{key} must be {expected} of at most {MAX_FIELD_BYTES} UTF-8 bytes")

    def choice[E: StrEnum](key: str, kind: type[E]) -> frozenset[E]:
        if key not in args:
            return frozenset()
        for member in kind:
            if args[key] == member.value:
                return frozenset([member])
        raise InvalidToolArguments(f"{key} must be one of {', '.join(member.value for member in kind)}")

    def note_id() -> NoteId:
        value = args.get("id")
        if isinstance(value, str):
            with contextlib.suppress(DomainError):
                return NoteId(value)
        raise InvalidToolArguments(f"id must be a kebab-case note id of at most {MAX_NOTE_ID_LENGTH} characters")

    def integer(key: str) -> int:
        schema = properties[key]
        value = args.get(key, schema["default"])
        # JSON Schema treats numbers such as 1.0 as integers. JSON booleans are a separate type.
        whole = type(value) is int or (type(value) is float and value.is_integer())
        if whole and schema["minimum"] <= value <= schema["maximum"]:
            return int(value)
        raise InvalidToolArguments(f"{key} must be an integer from {schema['minimum']} to {schema['maximum']}")

    if name == "cerebro_write_guide":
        return GetVocabularyRequest()
    if name == "cerebro_overview":
        return GetOverviewRequest()
    if name == "cerebro_search":
        include_history = args.get("include_history", False)
        if type(include_history) is not bool:
            raise InvalidToolArguments("include_history must be true or false")
        return SearchNotesRequest(
            text=text("query", limit=MAX_QUERY_BYTES),
            areas=kebab("area", Area),
            types=kebab("type", NoteType),
            priorities=choice("priority", Priority),
            statuses=choice("status", Status),
            include_history=include_history,
            limit=integer("limit"),
        )
    target = note_id()
    if name == "cerebro_neighbors":
        return GetNeighborsRequest(note_id=target, hops=integer("depth"))
    section = None
    if "section" in args:
        expected = "a non-blank string"
        section = text("section", expected)
        if not section.strip():
            raise InvalidToolArguments(f"section must be {expected} of at most {MAX_FIELD_BYTES} UTF-8 bytes")
    return ReadNoteRequest(note_id=target, section=section)


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


def _structured_error(details: dict[str, object]) -> types.CallToolResult:
    return types.CallToolResult(
        is_error=True,
        content=[types.TextContent(text=json.dumps(details, ensure_ascii=False))],
        structured_content=details,
    )


def tool_scope(name: str) -> Scope:
    return Scope.WRITE if name in {WRITE_TOOL.name, "cerebro_write_guide"} else Scope.READ


@dataclass(frozen=True, slots=True, kw_only=True)
class MCPTools:
    get_overview: ContextUseCase[GetOverviewRequest, Overview]
    search_notes: ContextUseCase[SearchNotesRequest, SearchResults]
    read_note: ContextUseCase[ReadNoteRequest, NoteView]
    get_neighbors: ContextUseCase[GetNeighborsRequest, GraphView]

    agent_write: ContextUseCase[AgentWriteRequest, AgentWriteResult]
    get_vocabulary: ContextUseCase[GetVocabularyRequest, Vocabulary]

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
            elif isinstance(request, GetVocabularyRequest):
                vocabulary = self.get_vocabulary.execute(request, ctx)
                output = {
                    "guide": read_write_guide(),
                    "areas": sorted(area.value for area in vocabulary.areas),
                    "types": sorted(kind.value for kind in vocabulary.types),
                }
            else:
                output = _neighbors(self.get_neighbors.execute(request, ctx))
            return types.CallToolResult(
                content=[types.TextContent(text=json.dumps(output, ensure_ascii=False))], structured_content=output
            )
        except InvalidToolArguments as error:
            return _error(str(error))
        except DomainError:
            return _error("Invalid tool arguments.")
        except WriteRejected as error:
            return _structured_error({"findings": _findings(error.findings)})
        except NoteNotFound:
            return _error("Not found.")
        except SectionNotFound as error:
            return _structured_error(
                {
                    "error": "Section not found.",
                    "headings": list(error.headings),
                    "headings_truncated": error.headings_truncated,
                }
            )
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
