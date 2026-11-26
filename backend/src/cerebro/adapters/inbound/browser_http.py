"""Dispatch browser traffic by the accepting socket. MCP retains its transport and lifespan."""

import secrets
from collections.abc import Mapping
from dataclasses import dataclass

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cerebro.application.browser import BrowserBinding


@dataclass(frozen=True, slots=True)
class BrowserListener:
    binding: BrowserBinding
    api: ASGIApp


def browser_http(mcp: ASGIApp, listeners: Mapping[int, BrowserListener], assets: ASGIApp) -> ASGIApp:
    sockets = dict(listeners)

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
        if listener is None or headers.getlist("host") != [listener.binding.origin.removeprefix("https://")]:
            await PlainTextResponse("Invalid request.", 400)(scope, receive, send_browser)
            return
        origins = headers.getlist("origin")
        if origins and origins != [listener.binding.origin]:
            await PlainTextResponse("Access denied.", 403)(scope, receive, send_browser)
            return
        app = listener.api if api_request else assets
        await app(browser_scope, receive, send_browser)

    return handle
