"""Record, follow and filter live agent activity (ADR-0031)."""

import contextlib

from cerebro.application.access import require_restricted_owner, stored_note
from cerebro.application.activity import (
    ActivityEvent,
    ActivitySubscription,
    FollowActivityRequest,
    RecordActivityRequest,
    VisibleActivityRequest,
)
from cerebro.application.errors import NoteReadError
from cerebro.application.ports.activity import ActivityFeed
from cerebro.application.ports.notes import NoteRepository
from cerebro.application.ports.system import Clock
from cerebro.domain.access import AccessContext, Agent


class RecordActivity:
    """Publish what an agent just did successfully. Its failures never reach the agent."""

    def __init__(self, feed: ActivityFeed, clock: Clock) -> None:
        self._feed = feed
        self._clock = clock

    def execute(self, request: RecordActivityRequest, ctx: AccessContext) -> None:
        if not isinstance(ctx.principal, Agent):
            return
        with contextlib.suppress(Exception):
            self._feed.publish(
                ActivityEvent(
                    note_id=request.note_id,
                    verb=request.verb,
                    agent=ctx.principal.name,
                    at=self._clock.now(),
                    sensitivity=request.sensitivity,
                )
            )


class FollowActivity:
    """Subscribe the owner, with the ceiling ``restricted``: a write event is change history."""

    def __init__(self, feed: ActivityFeed) -> None:
        self._feed = feed

    def execute(self, request: FollowActivityRequest, ctx: AccessContext) -> ActivitySubscription:
        require_restricted_owner(ctx, "follow agent activity")
        return self._feed.subscribe(ctx.ceiling, request.wake)


class VisibleActivity:
    """Whether an event may be delivered now: the note exists, can be read and was and is within the ceiling."""

    def __init__(self, notes: NoteRepository) -> None:
        self._notes = notes

    def execute(self, request: VisibleActivityRequest, ctx: AccessContext) -> bool:
        require_restricted_owner(ctx, "follow agent activity")
        try:
            note = stored_note(self._notes, request.event.note_id, ctx)
        except NoteReadError:
            return False
        return note is not None and all(
            ctx.ceiling.allows(level) for level in (note.sensitivity, request.event.sensitivity)
        )
