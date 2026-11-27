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
from concurrent.futures import ThreadPoolExecutor
from functools import partial
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
    # A receive channel that fails is a lost client too.
    with contextlib.suppress(Exception):
        while (await receive())["type"] != "http.disconnect":
            pass


STREAM_WORKERS: Final = 2
"""Session and visibility checks of a listener's streams share these threads, never the ones that serve agents."""


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
    workers = ThreadPoolExecutor(max_workers=STREAM_WORKERS, thread_name_prefix="activity-stream")

    async def work[Result](call: Callable[[], Result]) -> Result:
        return await asyncio.get_running_loop().run_in_executor(workers, call)

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
            await work(lambda: auth.check(binding, session_id))
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
        elif (
            scope["query_string"]
            or "transfer-encoding" in (headers := Request(scope).headers)
            or headers.get("content-length", "0") != "0"
        ):
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

        async def deliver(events: tuple[ActivityEvent, ...]) -> bool:
            """Send what is visible now; false once the session has ended."""
            for event in events:
                if not await session_open():
                    return False
                request = VisibleActivityRequest(event=event)
                if await work(partial(visible.execute, request, ctx)):
                    # Checked again: the session may have ended while the note was being read.
                    if not await session_open():
                        return False
                    await write(_frame(event))
            return True

        async def session_open() -> bool:
            try:
                await work(lambda: auth.check(binding, session_id))
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
                if events := subscription.take():
                    if not await deliver(events):
                        break
                    continue
                wait = max(0.0, min(poll, next_tick - loop.time(), started + max_age - loop.time()))
                waiter = asyncio.ensure_future(woken.wait())
                try:
                    await asyncio.wait({waiter, gone}, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    waiter.cancel()
        except Exception as error:
            log_failure("activity stream", error)
        finally:
            subscription.close()
            gone.cancel()
            with contextlib.suppress(Exception):
                await send({"type": "http.response.body", "body": b"", "more_body": False})

    return app
