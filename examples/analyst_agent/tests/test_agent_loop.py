"""Tests for `agent.py`: the mention loop that ties `smac_link.py`,
`brain.py`, `guard.py`, and `bus.py` together.

`FakeLink` and `FakeBrain` below are plain duck-typed doubles -- no real
network, no real Anthropic client, no `ANTHROPIC_API_KEY` needed. `FakeLink`
mimics `SmacLink`'s synchronous request methods (`history`/`post`/
`pending_mentions`/`ack`) plus an `events()` async generator scriptable
per-connection, so the reconnect/catch-up-drain discipline is exercised
without a real WebSocket. `FakeBrain.think()` mirrors `Brain.think()`'s
minimal observable footprint (`model_call` then `model_done` on the bus,
no `token` events) so the exact bus-event-kind assertions below match what
the real `Brain` would also produce for a non-streamed-looking fake reply.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from analyst_agent.agent import Agent, Disconnect, _default_backoff_seconds
from analyst_agent.brain import Reply
from analyst_agent.bus import Bus
from analyst_agent.guard import Guard
from analyst_agent.smac_link import Credentials

ME = "mem-me"
ALICE = "mem-alice"


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
def bus() -> Bus:
    return Bus()


@pytest.fixture()
def credentials() -> Credentials:
    return Credentials(
        member_id=ME,
        handle="analyst",
        api_key="smac-key-xyz-secret",
        workspace_id="ws-1",
        workspace_name="Test Workspace",
    )


class FakeLink:
    """A duck-typed `SmacLinkLike` double. `pending_batches`/
    `event_batches` are queues of "what the next call/connection
    returns" -- each `pending_mentions()` call pops the next batch (or
    returns `[]` once exhausted); each `events()` call pops the next
    connection's frame list (or ends immediately once exhausted)."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        pending_batches: list[list[dict[str, Any]]] | None = None,
        event_batches: list[list[dict[str, Any]]] | None = None,
    ) -> None:
        self.credentials: Credentials | None = credentials
        self.history_calls: list[tuple[str, int]] = []
        self.posted: list[tuple[str, str]] = []
        self.acked: list[str] = []
        self.pending_calls = 0
        self.events_calls = 0
        self._pending_batches = list(pending_batches or [])
        self._event_batches = list(event_batches or [])
        self._history_response: list[dict[str, Any]] = []

    def history(self, channel_id: str, limit: int = 20) -> list[dict[str, Any]]:
        self.history_calls.append((channel_id, limit))
        return list(self._history_response)

    def post(self, channel_id: str, text: str) -> dict[str, Any]:
        self.posted.append((channel_id, text))
        return {"Message": {"message_id": "sent-1", "message_text": text}}

    def pending_mentions(self) -> list[dict[str, Any]]:
        self.pending_calls += 1
        if self._pending_batches:
            return self._pending_batches.pop(0)
        return []

    def ack(self, mention_id: str) -> None:
        self.acked.append(mention_id)

    async def events(self):
        self.events_calls += 1
        batch = self._event_batches.pop(0) if self._event_batches else []
        for item in batch:
            yield item


class FakeBrain:
    """A duck-typed `BrainLike` double: always answers `text` and
    publishes exactly the two bus events the real `Brain.think()`
    publishes around a (here, instant, non-streamed) reply --
    `model_call` then `model_done`, no `token` events."""

    def __init__(self, bus: Bus, text: str = "FAKE REPLY") -> None:
        self.bus = bus
        self.text = text
        self.calls: list[dict[str, Any]] = []

    async def think(
        self,
        system: str,
        history: list[dict[str, str]],
        trigger: str,
        thread: list[dict[str, str]] | None = None,
    ) -> Reply:
        self.calls.append(
            {"system": system, "history": history, "trigger": trigger, "thread": thread}
        )
        self.bus.publish("model_call", model="fake-model", temp=1.0, context_size=1)
        self.bus.publish("model_done", input_tokens=1, output_tokens=1, seconds=0.001)
        return Reply(text=self.text, input_tokens=1, output_tokens=1, seconds=0.001)


def mention(
    mention_id: str = "m1",
    *,
    channel: str = "c1",
    sender: str = ALICE,
    sender_name: str = "Alice",
    text: str = "@analyst summarize",
) -> dict[str, Any]:
    """A mention frame in exactly `app/mentions.py`'s `build_mention_event`
    wire shape -- same shape whether it comes from `GET /mentions`'s drain
    or a live WebSocket frame."""
    return {
        "event": "mention",
        "mention_id": mention_id,
        "created_at": "2026-08-07T00:00:00Z",
        "mentioned_member_id": ME,
        "message": {
            "timestamp": "2026-08-07T00:00:00Z",
            "workspace": {"workspace_id": "ws-1", "workspace_name": "Test Workspace"},
            "Channel": {"channel_id": channel, "channel_name": "general"},
            "Sender": {"member_id": sender, "member_name": sender_name},
            "Message": {"message_id": "msg-1", "message_text": text},
            "mentions": [],
            "channel_refs": [],
        },
    }


@pytest.fixture()
def fake_link(credentials: Credentials) -> FakeLink:
    return FakeLink(credentials)


@pytest.fixture()
def fake_brain(bus: Bus) -> FakeBrain:
    return FakeBrain(bus)


@pytest.fixture()
def guard() -> Guard:
    return Guard(max_replies_per_min=6, max_hops=3)


@pytest.fixture()
def agent(fake_link: FakeLink, fake_brain: FakeBrain, guard: Guard, bus: Bus, cfg):
    return Agent(fake_link, fake_brain, guard, bus, cfg)


# -- handle_mention -----------------------------------------------------------


@pytest.mark.anyio
async def test_mention_produces_context_reply_and_ack(
    agent: Agent, fake_link: FakeLink, bus: Bus
) -> None:
    await agent.handle_mention(mention(channel="c1", text="@analyst summarize"))

    assert fake_link.history_calls == [("c1", 20)]
    assert fake_link.posted == [("c1", "FAKE REPLY")]
    assert fake_link.acked == ["m1"]
    assert [e.kind for e in bus.history(50)] == [
        "mention",
        "context",
        "model_call",
        "model_done",
        "posted",
        "acked",
    ]


@pytest.mark.anyio
async def test_guard_block_skips_post_but_still_acks(
    agent: Agent, fake_link: FakeLink, bus: Bus
) -> None:
    await agent.handle_mention(mention(sender=ME))

    assert fake_link.posted == [] and fake_link.acked == ["m1"]
    assert bus.history(50)[-1].fields["reason"] == "own message"


@pytest.mark.anyio
async def test_paused_agent_logs_but_does_not_post(
    agent: Agent, fake_link: FakeLink, bus: Bus
) -> None:
    agent.paused = True

    await agent.handle_mention(mention())

    assert fake_link.posted == []
    assert any(e.kind == "paused_skip" for e in bus.history(50))
    # Critical behavior: a mention handled while paused still gets acked --
    # it must never sit in the inbox forever just because the agent was
    # paused when it arrived.
    assert fake_link.acked == ["m1"]


@pytest.mark.anyio
async def test_duplicate_mention_id_is_ignored(
    agent: Agent, fake_link: FakeLink
) -> None:
    await agent.handle_mention(mention("m1"))
    await agent.handle_mention(mention("m1"))

    assert fake_link.posted == [("c1", "FAKE REPLY")]  # exactly one
    assert fake_link.acked == ["m1"]  # the redelivery is a silent no-op


@pytest.mark.anyio
async def test_known_agent_ids_grows_from_answered_senders(
    agent: Agent, guard: Guard
) -> None:
    await agent.handle_mention(mention(sender=ALICE))

    assert (
        ALICE in agent._known_agent_ids
    )  # noqa: SLF001 - internal state, deliberately checked


# -- chat -----------------------------------------------------------------


@pytest.mark.anyio
async def test_chat_uses_the_brain_but_never_posts_to_smac(
    agent: Agent, fake_link: FakeLink, bus: Bus
) -> None:
    reply = await agent.chat("what did alice ask?")

    assert reply == "FAKE REPLY" and fake_link.posted == []
    assert fake_link.acked == [] and fake_link.history_calls == []
    assert [e.kind for e in bus.history(50)][:2] == ["chat_in", "model_call"]


# -- reconnect / catch-up drain ----------------------------------------------


@pytest.mark.anyio
async def test_reconnect_drains_pending_mentions_once(
    credentials: Credentials, fake_brain: FakeBrain, guard: Guard, bus: Bus, cfg
) -> None:
    # catch-up-then-live: on (re)connect, GET /mentions is drained before
    # live frames. The drain returns "m1" only on the first call (nothing
    # left pending by the second) -- a real server would behave the same
    # way once the drained mention has been acked.
    fake_link = FakeLink(credentials, pending_batches=[[mention("m1")], []])
    agent = Agent(fake_link, fake_brain, guard, bus, cfg)

    await agent.run_once_with(events=[Disconnect(), mention("m2")])

    assert fake_link.pending_calls == 2  # once per connect
    assert fake_link.acked == ["m1", "m2"]  # m1 came from the drain, no duplicate


# -- security: no secret in any emitted event --------------------------------


@pytest.mark.anyio
async def test_no_secret_in_any_emitted_event(
    agent: Agent, fake_link: FakeLink, bus: Bus
) -> None:
    import json

    await agent.handle_mention(mention())
    await agent.chat("hello")

    dumped = json.dumps([e.fields for e in bus.history(50)])
    assert fake_link.credentials is not None
    assert fake_link.credentials.api_key not in dumped


# -- run(): real reconnect loop, injectable backoff, no real sleep ----------


@pytest.mark.anyio
async def test_run_reconnects_with_injected_backoff_and_no_real_sleep(
    credentials: Credentials, fake_brain: FakeBrain, guard: Guard, bus: Bus, cfg
) -> None:
    # First "connection" ends immediately with no frames (a clean close) --
    # the loop must reconnect (draining pending_mentions() again) rather
    # than giving up. Second connection carries the mention that `once=True`
    # stops on.
    link = FakeLink(
        credentials,
        pending_batches=[[], []],
        event_batches=[[], [mention("m2")]],
    )
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)  # never actually waits

    agent = Agent(
        link,
        fake_brain,
        guard,
        bus,
        cfg,
        sleep=fake_sleep,
        backoff_seconds=lambda attempt: 7.0,
    )

    await asyncio.wait_for(agent.run(once=True), timeout=5)

    assert link.acked == ["m2"]
    assert sleeps == [7.0]  # exactly one reconnect wait, and it was the fake
    assert any(e.kind == "disconnected" for e in bus.history(50))
    assert any(e.kind == "reconnected" for e in bus.history(50))


def test_default_backoff_seconds_stays_within_the_documented_bounds() -> None:
    # 1s -> 30s exponential with jitter: attempt 0 is at most 1s, and no
    # attempt ever exceeds the 30s ceiling.
    assert 0 <= _default_backoff_seconds(0) <= 1.0
    assert 0 <= _default_backoff_seconds(1) <= 2.0
    for attempt in (5, 10, 50):
        assert 0 <= _default_backoff_seconds(attempt) <= 30.0
