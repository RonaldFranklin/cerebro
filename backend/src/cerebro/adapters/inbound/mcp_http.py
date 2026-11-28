"""Stateless MCP over HTTP. The ASGI server address identifies the accepting socket."""

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import RequestBodyLimitMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, Router
from starlette.types import Message, Receive, Scope, Send

from cerebro.adapters.inbound.diagnostics import LOCAL_FAILURE, log_failure
from cerebro.adapters.inbound.input_limits import MAX_INPUT_BYTES
from cerebro.adapters.inbound.mcp_tools import InvalidToolArguments, MCPTools, parse_tool, tool_scope
from cerebro.application.dto import AuthenticateTokenRequest
from cerebro.application.errors import AuthenticationFailed, PermissionDenied
from cerebro.application.use_case import AnonymousUseCase
from cerebro.domain.access import AccessCeiling, AccessContext
from cerebro.domain.access import Scope as AgentScope

MAX_PENDING_OBSERVATIONS = 16
_BEARER = re.compile(r"Bearer ([a-z0-9_~-]{1,512})", re.ASCII | re.IGNORECASE)


def decode_frame(body: bytes) -> dict[str, object]:
    """Validate the client envelope and tool arguments before invoking any use case."""
    message = json.loads(body.decode("utf-8"))
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        raise ValueError("invalid message")
    if "id" in message and type(message["id"]) not in (int, str):
        raise ValueError("invalid id")
    if isinstance(message.get("id"), str):
        message["id"].encode("utf-8")
    return message


def invalid_arguments(rpc_id: object, error: InvalidToolArguments) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": str(error)}}


def validate_params(message: dict[str, object]) -> AgentScope | None:
    params = message.get("params", {})
    if not isinstance(params, dict):
        raise InvalidToolArguments("params must be an object")
    if message["method"] == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            raise InvalidToolArguments("name must be a string")
        parse_tool(name, params.get("arguments"))
        return tool_scope(name)
    return None


def http_app(
    tools: MCPTools,
    authenticate: AnonymousUseCase[AuthenticateTokenRequest, AccessContext],
    listeners: Mapping[int, AccessCeiling],
    observe: Callable[[AccessContext], None] | None = None,
) -> Router:
    ceilings = dict(listeners)

    def agent(raw: object) -> AccessContext:
        if not isinstance(raw, Request) or raw.scope.get("server") is None:
            raise AuthenticationFailed
        ceiling = ceilings.get(raw.scope["server"][1])
        values = raw.headers.getlist("authorization")
        credential = _BEARER.fullmatch(values[0]) if len(values) == 1 else None
        if ceiling is None or credential is None:
            raise AuthenticationFailed
        ctx = authenticate.execute(AuthenticateTokenRequest(secret=credential[1], listener=ceiling))
        if ctx.is_owner or not ctx.scopes:
            raise PermissionDenied("read")
        return ctx

    manager = StreamableHTTPSessionManager(
        tools.server(agent), stateless=True, json_response=True, max_request_body_size=MAX_INPUT_BYTES
    )

    # Observation runs beside the response on its own thread: a slow disk can neither delay an agent nor occupy the
    # workers that authenticate requests. Beyond the bound, observations are dropped.
    observer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="observe")
    pending: set[asyncio.Future[None]] = set()

    def finished(future: asyncio.Future[None]) -> None:
        pending.discard(future)
        if not future.cancelled() and (error := future.exception()) is not None:
            log_failure("mcp observation", error)

    def _observe(ctx: AccessContext) -> None:
        """Once for each authenticated request that is not refused for its scopes. The SDK authenticates again while
        serving it; those calls are not observed. An observer's failure never changes the response."""
        if observe is None or len(pending) >= MAX_PENDING_OBSERVATIONS:
            return
        future = asyncio.get_running_loop().run_in_executor(observer, observe, ctx)
        pending.add(future)
        future.add_done_callback(finished)

    async def handle(scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        # Authenticate before parsing the body: an anonymous caller only ever receives 401.
        try:
            ctx = await asyncio.to_thread(agent, request)
        except AuthenticationFailed:
            await Response("Access denied.", status_code=401, headers={"WWW-Authenticate": "Bearer"})(
                scope, receive, send
            )
            return
        except PermissionDenied:
            await Response("Access denied.", status_code=403)(scope, receive, send)
            return
        except Exception as error:
            log_failure("mcp authentication", error)
            # The body stays unread: an unauthenticated request is never parsed, so the error carries no id.
            await JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": LOCAL_FAILURE}},
                status_code=503,
            )(scope, receive, send)
            return
        rpc_id: object = None
        try:
            if scope["query_string"] or request.headers.get("origin") is not None:
                raise ValueError("unexpected request source")
            body = await request.body()
            if request.method == "POST":
                message = decode_frame(body)
                rpc_id = message.get("id")
                required_scope = validate_params(message)
                if required_scope is not None and not ctx.can(required_scope):
                    await Response("Access denied.", status_code=403)(scope, receive, send)
                    return
        except InvalidToolArguments as error:
            _observe(ctx)
            response: Response = JSONResponse(invalid_arguments(rpc_id, error), status_code=400)
        except ValueError, RecursionError:
            _observe(ctx)
            response = Response("Invalid request.", status_code=400)
        else:
            _observe(ctx)
            if request.method != "POST":
                response = Response(status_code=405, headers={"Allow": "POST"})
            elif "last-event-id" in request.headers or "mcp-session-id" in request.headers:
                response = Response("Session unavailable.", status_code=404)
            else:
                # The outer byte limiter already bounded and consumed the request. Replay it once to the SDK.
                sent_body = False

                async def replay() -> Message:
                    nonlocal sent_body
                    if sent_body:
                        return await receive()
                    sent_body = True
                    return {"type": "http.request", "body": body, "more_body": False}

                await manager.handle_request(scope, replay, send)
                return
        await response(scope, receive, send)

    @asynccontextmanager
    async def lifespan(app: Router) -> AsyncIterator[None]:
        try:
            async with manager.run():
                yield
        finally:
            if pending:
                await asyncio.wait(pending, timeout=1)
            observer.shutdown(wait=False, cancel_futures=True)

    return Router(
        routes=[Route("/mcp", RequestBodyLimitMiddleware(handle, MAX_INPUT_BYTES))],
        redirect_slashes=False,
        lifespan=lifespan,
    )
