"""Tests for `guard.py`'s citizenship rules: never reply to yourself,
always answer a human, cap agent-to-agent loop depth (reset by any human
message in between), and cap replies per minute -- all against an
injectable clock so the rate-cap test needs no real sleeps.

`Sender` mirrors the wire's `Message.Sender` field exactly
(`member_id`/`member_name` -- no type, no handle: SMAC's agent API keys
are capped so `GET /members` 403s, so nothing on the wire ever tells an
agent whether another sender is human or an agent). That's why
`Guard.check` takes `known_agent_ids` explicitly rather than trying to
infer it from the payload.
"""

from __future__ import annotations

import pytest

from analyst_agent.guard import Guard, Message, Sender

ME = "member-me"
ALICE_HUMAN = "member-alice"
AGENT_B = "member-agent-b"


def _sender(member_id: str) -> Sender:
    return Sender(member_id=member_id, member_name=member_id)


def mention_from(member_id: str) -> Message:
    """The incoming mention that would trigger a reply, as `Guard.check`
    needs it: just who sent it."""
    return Message(sender=_sender(member_id))


def msg_from(member_id: str) -> Message:
    """One entry of channel history preceding the mention (`recent`)."""
    return Message(sender=_sender(member_id))


class FakeClock:
    """A `time.monotonic`-shaped callable the test controls -- no real
    sleeps, no flakiness."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture()
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def guard(fake_clock: FakeClock) -> Guard:
    return Guard(max_replies_per_min=6, max_hops=3, clock=fake_clock)


def test_never_replies_to_itself(guard: Guard) -> None:
    d = guard.check(mention_from(ME), me_member_id=ME, recent=[])
    assert not d.allowed
    assert d.reason == "own message"


def test_replies_to_a_human(guard: Guard) -> None:
    d = guard.check(mention_from(ALICE_HUMAN), me_member_id=ME, recent=[])
    assert d.allowed
    assert d.reason is None


def test_agent_chain_stops_at_max_hops(guard: Guard) -> None:
    recent = [msg_from(AGENT_B), msg_from(ME), msg_from(AGENT_B)]  # depth 3
    d = guard.check(mention_from(AGENT_B), ME, recent=recent, known_agent_ids={AGENT_B})
    assert not d.allowed
    assert d.reason is not None and "loop depth" in d.reason


def test_a_human_message_resets_the_chain(guard: Guard) -> None:
    recent = [msg_from(AGENT_B), msg_from(ALICE_HUMAN), msg_from(AGENT_B)]
    d = guard.check(mention_from(AGENT_B), ME, recent=recent, known_agent_ids={AGENT_B})
    assert d.allowed


def test_rate_cap_blocks_the_seventh_reply_in_a_minute(
    guard: Guard, fake_clock: FakeClock
) -> None:
    for _ in range(6):
        guard.record_reply()
    d = guard.check(mention_from(ALICE_HUMAN), ME, recent=[])
    assert not d.allowed
    assert d.reason == "rate cap 6/min"

    fake_clock.advance(61)
    assert guard.check(mention_from(ALICE_HUMAN), ME, recent=[]).allowed


def test_loop_depth_below_cap_is_allowed(guard: Guard) -> None:
    """Two consecutive agent turns is still within `max_hops=3` -- only
    a *third* consecutive turn (this reply) would hit the cap next time,
    not this one."""
    recent = [msg_from(ME), msg_from(AGENT_B)]
    d = guard.check(mention_from(AGENT_B), ME, recent=recent, known_agent_ids={AGENT_B})
    assert d.allowed


def test_reason_is_none_when_allowed(guard: Guard) -> None:
    assert guard.check(mention_from(ALICE_HUMAN), ME, recent=[]).reason is None
