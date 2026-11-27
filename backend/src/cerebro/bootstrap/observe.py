"""The activity observers the composition root puts around what the MCP adapters call (ADR-0031)."""

import asyncio

from starlette.types import ASGIApp, Receive, Scope, Send

from cerebro.adapters.outbound.system.activity import MemoryActivityFeed
from cerebro.application.activity import RecordActivityRequest
from cerebro.application.agent_write import AgentWriteRequest, AgentWriteResult
from cerebro.application.dto import NoteView, ReadNoteRequest
from cerebro.application.use_case import ContextUseCase
from cerebro.application.use_cases import RecordActivity
from cerebro.domain.access import AccessContext


class ObservedRead:
    """A successful read by an agent is an event. The result and any error pass through unchanged."""

    def __init__(self, inner: ContextUseCase[ReadNoteRequest, NoteView], record: RecordActivity) -> None:
        self._inner = inner
        self._record = record

    def execute(self, request: ReadNoteRequest, ctx: AccessContext) -> NoteView:
        view = self._inner.execute(request, ctx)
        self._record.execute(RecordActivityRequest(note_id=view.id, verb="read", sensitivity=view.sensitivity), ctx)
        return view


class ObservedWrite:
    """A write that committed now is an event; a replayed create is not."""

    def __init__(self, inner: ContextUseCase[AgentWriteRequest, AgentWriteResult], record: RecordActivity) -> None:
        self._inner = inner
        self._record = record

    def execute(self, request: AgentWriteRequest, ctx: AccessContext) -> AgentWriteResult:
        result = self._inner.execute(request, ctx)
        if not result.replayed and result.sensitivity is not None:
            event = RecordActivityRequest(note_id=result.note_id, verb="write", sensitivity=result.sensitivity)
            self._record.execute(event, ctx)
        return result


def bind_on_startup(app: ASGIApp, feed: MemoryActivityFeed) -> ASGIApp:
    """Hand the feed the server's event loop when it starts."""

    async def handle(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            feed.bind(asyncio.get_running_loop().call_soon_threadsafe)
        await app(scope, receive, send)

    return handle
