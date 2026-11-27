"""Recording, following and filtering agent activity, over the real in-memory feed."""

from datetime import timedelta

import pytest

from cerebro.adapters.outbound.system.activity import RETAINED, MemoryActivityFeed
from cerebro.application.activity import (
    ActivityEvent,
    FollowActivityRequest,
    RecordActivityRequest,
    VisibleActivityRequest,
)
from cerebro.application.errors import NoteReadError, PermissionDenied
from cerebro.application.use_cases import FollowActivity, RecordActivity, VisibleActivity
from cerebro.domain.access import AccessCeiling, AccessContext
from cerebro.domain.enums import Sensitivity
from cerebro.domain.note import NoteId
from tests.fakes.system import FixedClock
from tests.unit.application.world import ctx, standard_world

OWNER = ctx(Sensitivity.RESTRICTED, owner=True)
SSH = NoteId("server-ssh-access")
LOWER = [ctx(Sensitivity.RESTRICTED), ctx(Sensitivity.INTERNAL, owner=True), ctx(Sensitivity.PUBLIC, owner=True)]


def event(note_id: str = "server-ssh-access", level: Sensitivity = Sensitivity.INTERNAL) -> ActivityEvent:
    return ActivityEvent(note_id=NoteId(note_id), verb="read", agent="codex", at=FixedClock().now(), sensitivity=level)


def test_an_agent_action_reaches_followers_with_its_name_and_time() -> None:
    feed, clock = MemoryActivityFeed(), FixedClock()
    woken: list[bool] = []
    subscription = FollowActivity(feed).execute(FollowActivityRequest(wake=lambda: woken.append(True)), OWNER)
    record = RecordActivity(feed, clock)
    record.execute(RecordActivityRequest(note_id=SSH, verb="write", sensitivity=Sensitivity.INTERNAL), ctx())
    clock.advance(timedelta(seconds=1))
    # The owner's own actions are not agent activity.
    record.execute(RecordActivityRequest(note_id=SSH, verb="read", sensitivity=Sensitivity.INTERNAL), OWNER)
    (seen,) = subscription.take()
    assert (seen.note_id, seen.verb, seen.agent, seen.at) == (SSH, "write", "claude-code", FixedClock().now())
    assert woken == [True]
    assert subscription.take() == ()


def test_a_failing_feed_never_reaches_the_agent() -> None:
    class Broken(MemoryActivityFeed):
        def publish(self, event: ActivityEvent) -> None:
            raise OSError("fixture")

    RecordActivity(Broken(), FixedClock()).execute(
        RecordActivityRequest(note_id=SSH, verb="read", sensitivity=Sensitivity.INTERNAL), ctx()
    )


def test_a_new_follower_receives_the_latest_events_only() -> None:
    feed = MemoryActivityFeed()
    for number in range(RETAINED + 5):
        feed.publish(event(f"note-{number}"))
    got = feed.subscribe(AccessCeiling(Sensitivity.RESTRICTED), lambda: None).take()
    assert [e.note_id.value for e in got] == [f"note-{number}" for number in range(5, RETAINED + 5)]


def test_a_full_queue_drops_its_oldest_and_hidden_events_take_no_place() -> None:
    feed = MemoryActivityFeed()
    woken: list[bool] = []
    internal = feed.subscribe(AccessCeiling(Sensitivity.INTERNAL), lambda: woken.append(True))
    feed.publish(event("visible-one"))
    for number in range(RETAINED * 2):
        feed.publish(event(f"hidden-{number}", Sensitivity.RESTRICTED))
    assert woken == [True]
    assert [e.note_id.value for e in internal.take()] == ["visible-one"]
    for number in range(RETAINED + 1):
        feed.publish(event(f"note-{number}"))
    assert internal.take()[0].note_id.value == "note-1"


def test_publishing_hands_off_to_the_bound_loop_and_survives_a_closed_one() -> None:
    feed = MemoryActivityFeed()
    pending: list[object] = []
    subscription = feed.subscribe(AccessCeiling(Sensitivity.RESTRICTED), lambda: None)
    feed.bind(pending.append)
    feed.publish(event())
    assert subscription.take() == ()
    pending[0]()  # type: ignore[operator]
    assert len(subscription.take()) == 1

    def closed(action: object) -> None:
        raise RuntimeError("Event loop is closed")

    feed.bind(closed)
    feed.publish(event())


def test_a_closed_subscription_receives_nothing_and_a_broken_one_stops_nobody() -> None:
    feed = MemoryActivityFeed()
    level = AccessCeiling(Sensitivity.RESTRICTED)

    def broken() -> None:
        raise RuntimeError("fixture")

    feed.subscribe(level, broken)
    closed = feed.subscribe(level, lambda: None)
    other = feed.subscribe(level, lambda: None)
    closed.close()
    feed.publish(event())
    assert closed.take() == ()
    assert len(other.take()) == 1


@pytest.mark.parametrize("context", LOWER)
def test_only_a_restricted_owner_follows_or_filters(context: AccessContext) -> None:
    world = standard_world()
    with pytest.raises(PermissionDenied):
        FollowActivity(MemoryActivityFeed()).execute(FollowActivityRequest(wake=lambda: None), context)
    with pytest.raises(PermissionDenied):
        VisibleActivity(world.notes).execute(VisibleActivityRequest(event=event()), context)


def test_only_a_note_that_can_be_read_now_is_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    world = standard_world()
    visible = VisibleActivity(world.notes)
    assert visible.execute(VisibleActivityRequest(event=event()), OWNER)
    assert not visible.execute(VisibleActivityRequest(event=event("absent-note")), OWNER)

    def unreadable(note_id: NoteId) -> None:
        raise NoteReadError("fixture")

    monkeypatch.setattr(world.notes, "get", unreadable)
    assert not visible.execute(VisibleActivityRequest(event=event()), OWNER)
