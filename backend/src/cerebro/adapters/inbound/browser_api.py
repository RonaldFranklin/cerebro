"""Owner browser JSON API for a single, immutable listener binding."""

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from mcp.server.transport_security import RequestBodyLimitMiddleware
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from cerebro.adapters.inbound.diagnostics import log_failure
from cerebro.adapters.inbound.input_limits import MAX_INPUT_BYTES
from cerebro.application.browser import AuthenticationFailed, BrowserAuth, BrowserBinding, CsrfRejected, SessionInfo
from cerebro.application.errors import NoteReadError
from cerebro.domain.access import AccessCeiling, AccessContext
from cerebro.domain.enums import Sensitivity
from cerebro.domain.errors import InvalidValueError

_NO_STORE = {"Cache-Control": "no-store"}
_SESSION_PATHS = frozenset({"/api/login", "/api/logout", "/api/session"})
_COOKIE_NAMES = {"private": "__Host-cerebro_private", "public": "__Host-cerebro_public"}


class BrowserBodyLimit:
    """Reject GET/HEAD bodies at the first nonempty frame and bound other requests before routing."""

    def __init__(self, app: ASGIApp) -> None:
        self._read = RequestBodyLimitMiddleware(app, max_body_size=0)
        self._write = RequestBodyLimitMiddleware(app, max_body_size=MAX_INPUT_BYTES)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        read = scope["type"] == "http" and scope["method"] in {"GET", "HEAD"}
        limiter = self._read if read else self._write
        await limiter(scope, receive, send)


def _route(request: Request) -> str:
    """The matched route's template (``/api/notes/{note_id}``), never the raw path."""
    path = getattr(request.scope.get("route"), "path", None)
    return path if isinstance(path, str) else "unmatched route"


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status, headers=_NO_STORE)


def _header(request: Request, name: str) -> str | None:
    values = request.headers.getlist(name)
    return values[0] if len(values) == 1 else None


def _session_cookie(request: Request, name: str) -> str | None:
    session_id: str | None = None
    for header in request.headers.getlist("cookie"):
        for item in header.split(";"):
            key, separator, value = item.strip().partition("=")
            if key == name:
                if not separator or session_id is not None or len(value) > 128:
                    raise AuthenticationFailed()
                session_id = value
    return session_id


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError
    return fields


def _invalid_number(value: str) -> object:
    raise ValueError("invalid JSON number")


def _check_json(body: bytes) -> None:
    value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_number)
    # Refuse lone surrogates, non-finite numbers and nested input outside the bounded write schema.
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    pending = [(value, 0)]
    while pending:
        entry, depth = pending.pop()
        if depth > 8:
            raise ValueError("excessive JSON depth")
        if isinstance(entry, dict):
            pending.extend((item, depth + 1) for item in entry.values())
        elif isinstance(entry, list):
            pending.extend((item, depth + 1) for item in entry)


def _password(body: bytes) -> bytes:
    fields = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(fields, dict) or fields.keys() != {"password"} or not isinstance(fields["password"], str):
        raise ValueError
    password = fields["password"].encode("utf-8")
    if not 1 <= len(password) <= 1024:
        raise ValueError
    return password


def browser_api(auth: BrowserAuth, binding: BrowserBinding, ceiling: AccessCeiling) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    cookie_name = _COOKIE_NAMES[binding.listener]

    def session_data(session: SessionInfo) -> dict[str, str | int]:
        return {
            "csrf_token": session.csrf_token,
            "ceiling": ceiling.level.value,
            "absolute_remaining_seconds": session.absolute_remaining_seconds,
        }

    @app.middleware("http")
    async def authenticate(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if len(request.scope["query_string"]) > 8192:
            return _error(422, "Invalid request.")
        path = request.scope["path"]
        if path in _SESSION_PATHS and request.scope["query_string"]:
            return _error(422, "Invalid request.")
        session_id = _session_cookie(request, cookie_name)
        if request.method == "POST" and path == "/api/login":
            if _header(request, "origin") != binding.origin or _header(request, "x-cerebro-request") != "1":
                return _error(403, "Access denied.")
            request.state.previous = session_id
        else:
            if not session_id:
                return _error(401, "Authentication required.")
            csrf = None
            if request.method not in {"GET", "HEAD"}:
                csrf = _header(request, "x-csrf-token")
                if _header(request, "origin") != binding.origin or not csrf:
                    return _error(403, "Access denied.")
            request.state.session_info = await asyncio.to_thread(auth.session, binding, session_id, csrf=csrf)
            request.state.ctx = AccessContext.for_owner(ceiling)
            request.state.session_id = session_id
            # Saving a note and checking a draft of it carry the note: both follow the write rules.
            note_write = path.startswith("/api/notes/") and request.method in {"PUT", "POST"}
            history = path == "/api/changes" or path.startswith("/api/changes/")
            if note_write or history:
                if ceiling.level is not Sensitivity.RESTRICTED:
                    return _error(403, "Access denied.")
                if request.scope["query_string"] and (
                    not history or len(request.query_params.getlist("cursor")) != 1 or len(request.query_params) != 1
                ):
                    return _error(422, "Invalid request.")
                if request.method not in {"GET", "HEAD"}:
                    media = _header(request, "content-type")
                    if not media or media.lower() not in {"application/json", "application/json; charset=utf-8"}:
                        return _error(415, "JSON required.")
                    try:
                        _check_json(await request.body())
                    except ValueError, RecursionError:
                        return _error(422, "Invalid request.")
        return await call_next(request)

    app.add_middleware(BrowserBodyLimit)

    @app.middleware("http")
    async def responses(request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            response = await call_next(request)
        except CsrfRejected:
            return _error(403, "Access denied.")
        except AuthenticationFailed as exc:
            if exc.retry_after:
                response = _error(429, "Try again later.")
                response.headers["Retry-After"] = str(exc.retry_after)
                return response
            return _error(401, "Authentication required.")
        except NoteReadError as exc:
            log_failure(f"{request.method} {_route(request)}", exc)
            return _error(503, "Some note files need attention: open Health.")
        except Exception as exc:
            # Only the route template and the exception class are logged: messages may carry note fields.
            log_failure(f"{request.method} {_route(request)}", exc)
            return _error(503, "Service unavailable.")
        if response.status_code == 413:
            if request.method in {"GET", "HEAD"}:
                return _error(422, "Invalid request.")
            return _error(413, "Request body too large.")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(InvalidValueError)
    async def invalid_input(request: Request, exc: Exception) -> Response:
        return _error(422, "Invalid request.")

    @app.post("/api/login")
    async def login(request: Request) -> Response:
        media = _header(request, "content-type")
        if not media or media.lower() not in {"application/json", "application/json; charset=utf-8"}:
            return _error(415, "JSON required.")
        try:
            password = _password(await request.body())
        except ValueError, RecursionError:
            return _error(422, "Invalid request.")
        issued = await asyncio.to_thread(auth.login, binding, password, request.state.previous)
        response = JSONResponse(session_data(issued))
        response.set_cookie(cookie_name, issued.session_id, path="/", httponly=True, secure=True, samesite="strict")
        return response

    @app.get("/api/session")
    async def session(request: Request) -> Response:
        return JSONResponse(session_data(request.state.session_info))

    @app.post("/api/logout")
    async def logout(request: Request) -> Response:
        if await request.body():
            return _error(422, "Invalid request.")
        await asyncio.to_thread(auth.logout, binding, request.state.session_id, request.state.session_info.csrf_token)
        response = Response(status_code=204)
        response.delete_cookie(cookie_name, path="/", httponly=True, secure=True, samesite="strict")
        return response

    return app
