"""The stream handler's own rules, driven over ASGI with fakes for its collaborators."""

import asyncio
import threading
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Any, cast

from cerebro.adapters.inbound.browser_activity import activity_stream
from cerebro.adapters.outbound.system.activity import MemoryActivityFeed
from cerebro.application.activity import ActivityEvent, VisibleActivityRequest
from cerebro.application.browser import AuthenticationFailed, BrowserAuth, BrowserBinding
from cerebro.application.use_cases import FollowActivity
from cerebro.domain.access import AccessCeiling, AccessContext
from cerebro.domain.enums import Sensitivity
from cerebro.domain.note import NoteId

BINDING = BrowserBinding("private", "https://private.example.invalid")
SESSION = "s" * 43


class FakeAuth:
    def __init__(self) -> None:
        self.open = True
        self.threads: set[str] = set()

    def check(self, binding: BrowserBinding, session_id: str) -> None:
        self.threads.add(threading.current_thread().name)
        if not self.open:
            raise AuthenticationFailed()


async def _api(scope: Any, receive: Any, send: Any) -> None:
    raise AssertionError("the stream path reached the API")


def drive(auth: FakeAuth, visible: Any, headers: list[tuple[bytes, bytes]] | None = None) -> list[Any]:
    feed = MemoryActivityFeed()
    for number in range(5):
        feed.publish(
            ActivityEvent(
                note_id=NoteId(f"note-{number}"),
                verb="read",
                agent="codex",
                at=datetime(2026, 11, 27, tzinfo=UTC),
                sensitivity=Sensitivity.INTERNAL,
            )
        )
    app = activity_stream(
        _api,
        cast(BrowserAuth, auth),
        BINDING,
        AccessCeiling(Sensitivity.RESTRICTED),
        FollowActivity(feed),
        visible,
        lambda: False,
        max_age=1.0,
        poll=0.05,
    )
    sent: list[Any] = []

    async def run() -> None:
        async def receive() -> dict[str, Any]:
            await asyncio.sleep(60)
            return {"type": "http.disconnect"}

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(message)

        cookie = (b"cookie", b"__Host-cerebro_private=" + SESSION.encode())
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/activity",
            "query_string": b"",
            "headers": [cookie, *(headers or [])],
        }
        await asyncio.wait_for(app(scope, receive, send), 10)

    asyncio.run(run())
    return sent


class Visible:
    def __init__(self, auth: FakeAuth, *, logout_after: int | None = None) -> None:
        self.auth = auth
        self.logout_after = logout_after
        self.calls = 0
        self.threads: set[str] = set()

    def execute(self, request: VisibleActivityRequest, ctx: AccessContext) -> bool:
        self.calls += 1
        self.threads.add(threading.current_thread().name)
        if self.calls == self.logout_after:
            self.auth.open = False
        return True


def frames(sent: list[Any]) -> int:
    return sum(b"data:" in message.get("body", b"") for message in sent)


def test_nothing_is_sent_after_the_session_ends_during_a_batch() -> None:
    auth = FakeAuth()
    assert frames(drive(auth, Visible(auth, logout_after=3))) == 2


def test_stream_work_runs_on_its_own_threads() -> None:
    auth = FakeAuth()
    visible = Visible(auth)
    assert frames(drive(auth, visible)) == 5
    for name in auth.threads | visible.threads:
        assert name.startswith("activity-stream")


def test_a_request_body_is_refused() -> None:
    for header in ((b"content-length", b"5"), (b"transfer-encoding", b"chunked")):
        auth = FakeAuth()
        sent = drive(auth, Visible(auth), [header])
        assert sent[0]["status"] == 422
        assert frames(sent) == 0
