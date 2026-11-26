"""Dispatch browser traffic by the accepting socket. MCP retains its transport and lifespan."""

import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cerebro.application.browser import BrowserBinding


@dataclass(frozen=True, slots=True)
class BrowserListener:
    binding: BrowserBinding
    api: ASGIApp


def browser_http(mcp: ASGIApp, listeners: Mapping[int, BrowserListener], assets: ASGIApp) -> ASGIApp:
    sockets = dict(listeners)
    authorities = {}
    for listener in sockets.values():
        origin = urlsplit(listener.binding.origin)
        authorities[listener.binding] = (
            {origin.netloc, f"{origin.netloc}:443"} if origin.port is None else {origin.netloc}
        )

    async def handle(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/mcp":
            await mcp(scope, receive, send)
            return
        nonce = secrets.token_urlsafe(24)
        browser_scope = {**scope, "cerebro.style_nonce": nonce}
        path: str = scope["path"]
        api_request = path == "/api" or path.startswith("/api/")

        async def send_browser(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if api_request or "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Content-Security-Policy"] = (
                    "default-src 'none'; script-src 'self'; "
                    f"style-src 'self' 'nonce-{nonce}'; style-src-attr 'none'; "
                    "connect-src 'self'; img-src 'self'; font-src 'self'; worker-src 'self'; "
                    "frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'"
                )
            await send(message)

        socket = scope.get("server")
        listener = sockets.get(socket[1]) if socket else None
        headers = Headers(scope=scope)
        hosts = headers.getlist("host")
        origins = headers.getlist("origin")
        if listener is None or len(hosts) != 1 or hosts[0].lower() not in authorities[listener.binding]:
            status, message = 400, "Invalid request."
        elif origins and origins != [listener.binding.origin]:
            status, message = 403, "Access denied."
        else:
            app = listener.api if api_request else assets
            await app(browser_scope, receive, send_browser)
            return
        response: Response = (
            JSONResponse({"error": message}, status) if api_request else PlainTextResponse(message, status)
        )
        await response(scope, receive, send_browser)

    return handle
