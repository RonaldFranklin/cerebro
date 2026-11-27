"""``GET /api/activity``: the owner's live stream of agent activity as server-sent events (ADR-0031).

A raw ASGI handler in front of the listener's API, because the stream outlives a request/response cycle. It
authenticates without renewing the session, serves a ``restricted`` owner only, bounds the open streams, repeats the
session check while open and ends on logout, expiry, password change, disconnect, shutdown or its maximum age.
Nothing about events or sessions is logged.
"""

import asyncio
import contextlib
import json
from collections.abc import Callable
from typing import Final

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from cerebro.adapters.inbound.browser_api import COOKIE_NAMES, session_cookie
from cerebro.adapters.inbound.diagnostics import log_failure
from cerebro.application.activity import (
    ActivityEvent,
    ActivitySubscription,
    FollowActivityRequest,
    VisibleActivityRequest,
)
from cerebro.application.browser import AuthenticationFailed, BrowserAuth, BrowserBinding
from cerebro.application.use_case import ContextUseCase
from cerebro.domain.access import AccessCeiling, AccessContext
from cerebro.domain.enums import Sensitivity

PATH: Final = "/api/activity"
MAX_STREAMS: Final = 4
TICK_SECONDS: Final = 15.0
MAX_AGE_SECONDS: Final = 3600.0
POLL_SECONDS: Final = 1.0
"""How often an idle stream looks at the shutdown signal."""


def _frame(event: ActivityEvent) -> bytes:
    data = {"note_id": event.note_id.value, "verb": event.verb, "client": event.agent, "at": event.at.isoformat()}
    return f"event: activity\ndata: {json.dumps(data)}\n\n".encode()


async def _disconnected(receive: Receive) -> None:
    while (await receive())["type"] != "http.disconnect":
        pass


def activity_stream(
    api: ASGIApp,
    auth: BrowserAuth,
    binding: BrowserBinding,
    ceiling: AccessCeiling,
    follow: ContextUseCase[FollowActivityRequest, ActivitySubscription],
    visible: ContextUseCase[VisibleActivityRequest, bool],
    stopping: Callable[[], bool],
    *,
    tick: float = TICK_SECONDS,
    max_age: float = MAX_AGE_SECONDS,
    poll: float = POLL_SECONDS,
) -> ASGIApp:
    """``api`` with the activity stream of one listener in front of it."""
    cookie = COOKIE_NAMES[binding.listener]
    open_streams = 0

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal open_streams
        if scope["type"] != "http" or scope["path"] != PATH:
            await api(scope, receive, send)
            return

        async def refuse(status: int, message: str) -> None:
            await JSONResponse({"error": message}, status)(scope, receive, send)

        # Authentication comes first: an anonymous caller learns nothing and holds no slot.
        try:
            session_id = session_cookie(Request(scope), cookie)
            if not session_id:
                raise AuthenticationFailed()
            await asyncio.to_thread(auth.check, binding, session_id)
        except AuthenticationFailed:
            await refuse(401, "Authentication required.")
            return
        except Exception as error:
            log_failure("activity authentication", error)
            await refuse(503, "Service unavailable.")
            return
        if ceiling.level is not Sensitivity.RESTRICTED:
            await refuse(403, "Access denied.")
        elif scope["method"] != "GET":
            await refuse(405, "Method not allowed.")
        elif scope["query_string"]:
            await refuse(422, "Invalid request.")
        elif open_streams >= MAX_STREAMS:
            await refuse(429, "Too many open streams.")
        else:
            open_streams += 1
            try:
                await stream(session_id, receive, send)
            finally:
                open_streams -= 1

    async def stream(session_id: str, receive: Receive, send: Send) -> None:
        loop = asyncio.get_running_loop()
        woken = asyncio.Event()
        ctx = AccessContext.for_owner(ceiling)

        def wake() -> None:
            loop.call_soon_threadsafe(woken.set)

        subscription = follow.execute(FollowActivityRequest(wake=wake), ctx)
        gone = asyncio.ensure_future(_disconnected(receive))

        async def write(body: bytes) -> None:
            await send({"type": "http.response.body", "body": body, "more_body": True})

        async def session_open() -> bool:
            try:
                await asyncio.to_thread(auth.check, binding, session_id)
            except Exception:
                return False
            return True

        try:
            headers = [(b"content-type", b"text/event-stream; charset=utf-8"), (b"x-accel-buffering", b"no")]
            await send({"type": "http.response.start", "status": 200, "headers": headers})
            await write(b": open\n\n")
            started = loop.time()
            # A fixed clock, independent of events, so filtered ones change no timing.
            next_tick = started + tick
            while not (stopping() or gone.done() or loop.time() - started >= max_age):
                if loop.time() >= next_tick:
                    if not await session_open():
                        break
                    await write(b": keep-alive\n\n")
                    next_tick += tick
                woken.clear()
                events = subscription.take()
                if events:
                    if not await session_open():
                        break
                    for event in events:
                        request = VisibleActivityRequest(event=event)
                        if await asyncio.to_thread(visible.execute, request, ctx):
                            await write(_frame(event))
                    continue
                wait = max(0.0, min(poll, next_tick - loop.time(), started + max_age - loop.time()))
                waiter = asyncio.ensure_future(woken.wait())
                await asyncio.wait({waiter, gone}, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
                waiter.cancel()
        except Exception as error:
            log_failure("activity stream", error)
        finally:
            subscription.close()
            gone.cancel()
            with contextlib.suppress(Exception):
                await send({"type": "http.response.body", "body": b"", "more_body": False})

    return app
