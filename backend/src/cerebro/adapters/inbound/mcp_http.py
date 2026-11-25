"""Stateless MCP over HTTP. The ASGI server address identifies the accepting socket."""

import asyncio
import json
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import RequestBodyLimitMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, Router
from starlette.types import Message, Receive, Scope, Send

from cerebro.adapters.inbound.input_limits import MAX_INPUT_BYTES
from cerebro.adapters.inbound.mcp_tools import InvalidToolArguments, ReadTools, parse_tool
from cerebro.application.dto import AuthenticateTokenRequest
from cerebro.application.errors import AuthenticationFailed, PermissionDenied
from cerebro.application.use_case import AnonymousUseCase
from cerebro.domain.access import AccessCeiling, AccessContext
from cerebro.domain.access import Scope as AgentScope

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


def invalid_arguments(rpc_id: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32602, "message": "Invalid tool arguments."}}


def validate_params(message: dict[str, object]) -> None:
    params = message.get("params", {})
    if not isinstance(params, dict):
        raise InvalidToolArguments
    if message["method"] == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            raise InvalidToolArguments
        parse_tool(name, params.get("arguments"))


def http_app(
    tools: ReadTools,
    authenticate: AnonymousUseCase[AuthenticateTokenRequest, AccessContext],
    listeners: Mapping[int, AccessCeiling],
) -> Router:
    ceilings = dict(listeners)

    def reader(raw: object) -> AccessContext:
        if not isinstance(raw, Request) or raw.scope.get("server") is None:
            raise AuthenticationFailed
        ceiling = ceilings.get(raw.scope["server"][1])
        values = raw.headers.getlist("authorization")
        credential = _BEARER.fullmatch(values[0]) if len(values) == 1 else None
        if ceiling is None or credential is None:
            raise AuthenticationFailed
        ctx = authenticate.execute(AuthenticateTokenRequest(secret=credential[1], listener=ceiling))
        if ctx.is_owner or not ctx.can(AgentScope.READ):
            raise PermissionDenied("read")
        return ctx

    manager = StreamableHTTPSessionManager(
        tools.server(reader), stateless=True, json_response=True, max_request_body_size=MAX_INPUT_BYTES
    )

    async def handle(scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        rpc_id: object = None
        try:
            if scope["query_string"] or request.headers.get("origin") is not None:
                raise ValueError("unexpected request source")
            body = await request.body()
            if request.method == "POST":
                message = decode_frame(body)
                rpc_id = message.get("id")
                validate_params(message)
        except InvalidToolArguments:
            response: Response = JSONResponse(invalid_arguments(rpc_id), status_code=400)
        except ValueError, RecursionError:
            response = Response("Invalid request.", status_code=400)
        else:
            try:
                await asyncio.to_thread(reader, request)
            except AuthenticationFailed:
                await Response("Access denied.", status_code=401, headers={"WWW-Authenticate": "Bearer"})(
                    scope, receive, send
                )
                return
            except PermissionDenied:
                await Response("Access denied.", status_code=403)(scope, receive, send)
                return
            except Exception:
                await JSONResponse(
                    {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32603, "message": "Cérebro offline"}},
                    status_code=503,
                )(scope, receive, send)
                return
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
        async with manager.run():
            yield

    return Router(
        routes=[Route("/mcp", RequestBodyLimitMiddleware(handle, MAX_INPUT_BYTES))],
        redirect_slashes=False,
        lifespan=lifespan,
    )
