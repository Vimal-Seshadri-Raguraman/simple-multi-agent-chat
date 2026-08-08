"""Tests for `bus.py`: publish/subscribe delivery, fan-out to multiple
independent subscribers, and the bounded history backlog that lets a
late-attaching view (e.g. switching to the inner-view pane after the
agent has been running a while) see recent activity instead of a blank
pane.
"""

from __future__ import annotations

import asyncio

import pytest

from analyst_agent.bus import BUS_HISTORY_MAX, Bus


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_publish_then_subscribe_delivers_the_event() -> None:
    bus = Bus()
    subscription = bus.subscribe()
    pending = asyncio.ensure_future(subscription.__anext__())
    await asyncio.sleep(0)  # let the generator run up to its first await

    bus.publish("tick", n=1)
    event = await pending

    assert event.kind == "tick"
    assert event.fields == {"n": 1}
    await subscription.aclose()


@pytest.mark.anyio
async def test_two_subscribers_each_receive_every_event() -> None:
    bus = Bus()
    sub_a = bus.subscribe()
    sub_b = bus.subscribe()
    pending_a = asyncio.ensure_future(sub_a.__anext__())
    pending_b = asyncio.ensure_future(sub_b.__anext__())
    await asyncio.sleep(0)

    bus.publish("tick", n=1)
    event_a = await pending_a
    event_b = await pending_b

    assert event_a.kind == "tick" and event_a.fields == {"n": 1}
    assert event_b.kind == "tick" and event_b.fields == {"n": 1}
    await sub_a.aclose()
    await sub_b.aclose()


def test_history_returns_the_last_n_events_in_order() -> None:
    bus = Bus()
    for i in range(5):
        bus.publish("tick", n=i)

    events = bus.history(3)

    assert [e.fields["n"] for e in events] == [2, 3, 4]


def test_history_backlog_is_bounded() -> None:
    bus = Bus()
    for i in range(1000):
        bus.publish("tick", n=i)

    events = bus.history(10_000)

    assert len(events) == BUS_HISTORY_MAX
    # bounded means the *oldest* entries fall off, not the newest -- a
    # late-attaching view should see what just happened, not ancient history.
    assert events[-1].fields["n"] == 999
