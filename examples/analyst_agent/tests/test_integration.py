"""The deliverable integration journey (Task 6): a real, spawned SMAC
server, a founder who mints an agent invite code, an agent that redeems
it, joins `#general` (a required onboarding step -- see
`_add_agent_to_general`'s docstring: `/agents/join` mints a workspace
membership only, never a channel one) and answers a live mention, and
the two assertions that prove the loop actually closed -- the reply
lands in the channel, and the mention is acked.

Two variants of the same journey (design doc §6, "Integration"):

- `test_the_agent_answers_a_live_mention_with_a_fake_brain` -- the
  deliverable. A duck-typed `FakeBrain` (no `anthropic` import, no
  network) stands in for `brain.py`'s `Brain`, so this test needs no
  `ANTHROPIC_API_KEY` and makes no external call -- the only real thing
  it talks to is the SMAC server, spawned locally by this package's own
  `real_smac_server` fixture (`conftest.py`). Runs in the normal suite.
- `test_the_agent_answers_a_live_mention_with_the_real_brain` -- the
  same journey end to end, this time with the real `Brain` calling the
  real Anthropic API. `@pytest.mark.live` (registered in this package's
  `conftest.py`, never in the server's `pyproject.toml`), skipped with a
  clear reason when `ANTHROPIC_API_KEY` is absent.

Both variants exercise the LIVE push path, not the catch-up drain: the
agent's `run(once=True)` is already past its empty drain and blocked
inside `link.events()` -- a real WebSocket connected to
`/ws/workspaces/{ws}/members/me/events` -- before the human's message
(and the mention it creates) exist at all. That is the order events
happen in for a real running agent, and it is what makes this an
end-to-end proof rather than a replay of an already-queued mention. The
reply and the ack are both read back afterward over the real REST API,
from a second, independent `httpx.Client` playing the human -- nothing
here mocks the server away.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest

import analyst_agent.smac_link as smac_link_module
from analyst_agent.agent import Agent, BrainLike
from analyst_agent.brain import Brain, Reply
from analyst_agent.bus import Bus
from analyst_agent.config import Config
from analyst_agent.guard import Guard
from analyst_agent.smac_link import SmacLink

_TEST_PASSWORD = "integration-test-password-123"

#: How long the journey waits for the agent to answer end to end (its own
#: WS connect + the human's post + the model call + the agent's own
#: post/ack) before failing -- generous for the live variant's real
#: Anthropic round trip.
_JOURNEY_TIMEOUT_SECONDS = 30.0

#: How long to let `agent.run(once=True)` reach a live WebSocket connection
#: before the human posts the mention -- see the module docstring: this is
#: what makes the journey exercise the live push path, not the catch-up
#: drain. Generous for a loopback connection.
_CONNECT_GRACE_SECONDS = 0.5


class FakeBrain:
    """A duck-typed `BrainLike` double -- no `anthropic` import, no
    network, no `ANTHROPIC_API_KEY`. Mirrors the real `Brain.think()`'s
    minimal observable footprint (`model_call` then `model_done` on the
    bus, matching `test_agent_loop.py`'s own `FakeBrain`) so this
    journey's agent sees the same event shape a real streamed reply
    would produce."""

    def __init__(self, bus: Bus, text: str) -> None:
        self.bus = bus
        self.text = text

    async def think(
        self,
        system: str,
        history: list[dict[str, str]],
        trigger: str,
        thread: list[dict[str, str]] | None = None,
    ) -> Reply:
        self.bus.publish("model_call", model="fake-brain", temp=1.0, context_size=0)
        self.bus.publish("model_done", input_tokens=0, output_tokens=0, seconds=0.0)
        return Reply(text=self.text, input_tokens=0, output_tokens=0, seconds=0.0)


def _create_account(client: httpx.Client, email: str) -> dict[str, Any]:
    response = client.post(
        "/accounts", json={"email": email, "password": _TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _found_workspace(client: httpx.Client, workspace_name: str) -> dict[str, Any]:
    """Create a fresh human account and found a workspace with it over
    the real API -- the founder becomes the workspace's admin
    (`Cap.MINT_AGENT_INVITES` included), exactly like every other
    workspace in this repo. Returns the founder's bearer token, the
    workspace id, and `#general`'s channel id."""
    account = _create_account(client, f"{workspace_name}@test.example")
    account_token = account["tokens"]["access_token"]
    response = client.post(
        "/workspaces",
        json={
            "workspace_name": workspace_name,
            "visibility": "public",
            "display_first_name": "Founder",
            "display_last_name": "Human",
        },
        headers={"Authorization": f"Bearer {account_token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    founder_token = str(body["access_token"])
    workspace_id = str(body["workspace"]["workspace_id"])

    channels = client.get(
        f"/workspaces/{workspace_id}/channels",
        headers={"Authorization": f"Bearer {founder_token}"},
    )
    assert channels.status_code == 200, channels.text
    general = next(c for c in channels.json() if c["channel_name"] == "general")

    return {
        "founder_token": founder_token,
        "workspace_id": workspace_id,
        "general_channel_id": str(general["channel_id"]),
    }


def _mint_agent_code(
    client: httpx.Client, founder_token: str, workspace_id: str
) -> str:
    """`POST /workspaces/{id}/invites {"invite_type": "agent_code"}` --
    the exact call the web UI's Settings -> Invites makes, and the one
    the README's quickstart tells an operator to use."""
    response = client.post(
        f"/workspaces/{workspace_id}/invites",
        json={"invite_type": "agent_code"},
        headers={"Authorization": f"Bearer {founder_token}"},
    )
    assert response.status_code == 200, response.text
    code = response.json()["code"]
    assert code
    return str(code)


def _add_agent_to_general(
    client: httpx.Client,
    founder_token: str,
    workspace_id: str,
    channel_id: str,
    member_id: str,
) -> None:
    """`POST /workspaces/{ws}/channels/{ch}/members {"member_id": ...}` --
    the step a human operator must take after an agent redeems its
    invite code. Unlike a human founding/registering into a workspace
    (`create_member_account` auto-adds them to the workspace's default
    channel), `/agents/join` mints only a workspace membership: an
    agent/bot is a member of ZERO channels until a human explicitly adds
    it to one (`app/routers/members.py`'s `_register_member`, shared by
    every agent/bot door, never touches `ChannelMember`). Without this
    call, the agent's own `link.history()`/`link.post()` 403 with "not a
    member of channel" the moment it tries to act on a mention -- this
    is a REQUIRED step of the real onboarding journey, not an extra
    the example is adding on top, which is exactly why the README's
    Limitations section calls it out."""
    response = client.post(
        f"/workspaces/{workspace_id}/channels/{channel_id}/members",
        json={"member_id": member_id},
        headers={"Authorization": f"Bearer {founder_token}"},
    )
    assert response.status_code == 200, response.text


BrainFactory = Callable[[Bus], BrainLike]


async def _run_the_journey(
    base_url: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    *,
    brain_factory: BrainFactory,
    trigger_suffix: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The shared journey both variants run: found a workspace, mint an
    agent code, join the agent, start its mention loop, have the human
    mention it in `#general`, wait for the one-mention `--once` run to
    finish, and return (channel messages afterward, this agent's still-
    pending mentions) for the caller to assert against.

    `brain_factory(bus) -> BrainLike` is the one thing that differs
    between the fake and live variants; everything else about the
    journey -- account, workspace, invite, join, connect, mention, reply,
    ack -- is identical and real either way.
    """
    monkeypatch.setattr(smac_link_module, "CONFIG_HOME", tmp_path)

    workspace_name = f"analyst-integration-{uuid.uuid4().hex[:8]}"

    with httpx.Client(base_url=base_url, timeout=10.0) as human_client:
        setup = _found_workspace(human_client, workspace_name)
        code = _mint_agent_code(
            human_client, setup["founder_token"], setup["workspace_id"]
        )

        config = Config(
            smac_url=base_url,
            agent_name="Analyst",
            agent_code=code,
            anthropic_api_key="sk-ant-unused-in-this-test",
            model="claude-sonnet-5",
            max_replies_per_min=6,
            max_hops=3,
        )
        bus = Bus()
        link = SmacLink(config)
        link.join_or_load()
        assert link.credentials is not None
        handle = link.credentials.handle
        api_key = link.credentials.api_key

        # Required step (see the helper's docstring): the agent joins the
        # WORKSPACE via /agents/join, but no channel -- a human has to add
        # it to #general before it can read history or post there.
        _add_agent_to_general(
            human_client,
            setup["founder_token"],
            setup["workspace_id"],
            setup["general_channel_id"],
            link.credentials.member_id,
        )

        brain = brain_factory(bus)
        guard = Guard(config.max_replies_per_min, config.max_hops)
        agent = Agent(link, brain, guard, bus, config)

        # Start the agent's `--once` mention loop: it drains (nothing
        # pending yet), then blocks inside a real `link.events()`
        # WebSocket connect. Give it a moment to actually get there
        # before the human posts -- see the module docstring.
        agent_task: asyncio.Task[None] = asyncio.ensure_future(agent.run(once=True))
        try:
            await asyncio.sleep(_CONNECT_GRACE_SECONDS)

            human_message = f"@{handle} {trigger_suffix}"

            def _post_human_message() -> httpx.Response:
                return human_client.post(
                    f"/workspaces/{setup['workspace_id']}"
                    f"/channels/{setup['general_channel_id']}/messages",
                    json={"message_text": human_message},
                    headers={"Authorization": f"Bearer {setup['founder_token']}"},
                )

            posted = await asyncio.to_thread(_post_human_message)
            assert posted.status_code == 200, posted.text

            await asyncio.wait_for(agent_task, timeout=_JOURNEY_TIMEOUT_SECONDS)
        finally:
            if not agent_task.done():
                agent_task.cancel()

        messages_response = human_client.get(
            f"/workspaces/{setup['workspace_id']}"
            f"/channels/{setup['general_channel_id']}/messages",
            params={"limit": 15},
            headers={"Authorization": f"Bearer {setup['founder_token']}"},
        )
        assert messages_response.status_code == 200, messages_response.text

        mentions_response = human_client.get(
            "/mentions", headers={"X-API-Key": api_key}
        )
        assert mentions_response.status_code == 200, mentions_response.text

        return list(messages_response.json()), list(mentions_response.json())


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_the_agent_answers_a_live_mention_with_a_fake_brain(
    real_smac_server: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The deliverable: no `ANTHROPIC_API_KEY`, no network beyond the
    spawned SMAC server. Proves the whole loop -- join, live mention
    delivery, reply, ack -- closes for real against a real server."""
    reply_text = "FAKE REPLY: ignition test looks nominal, no anomalies."

    messages, pending_mentions = await _run_the_journey(
        real_smac_server,
        monkeypatch,
        tmp_path,
        brain_factory=lambda bus: FakeBrain(bus, reply_text),
        trigger_suffix="what's the status of the ignition test?",
    )

    reply_messages = [m for m in messages if m["Message"]["message_text"] == reply_text]
    assert len(reply_messages) == 1, messages
    assert reply_messages[0]["Sender"]["member_name"] == "Analyst"

    # GET /mentions no longer lists it: acked.
    assert pending_mentions == []


@pytest.mark.live
@pytest.mark.anyio
async def test_the_agent_answers_a_live_mention_with_the_real_brain(
    real_smac_server: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Same journey, real `Brain`, real Anthropic API. Skipped (never
    failed) with a clear reason when there's no key to call it with --
    the fake-brain variant above is what runs in the normal suite."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set -- skipping the live Anthropic journey")

    def _real_brain(bus: Bus) -> Brain:
        return Brain(api_key, "claude-sonnet-5", bus)

    messages, pending_mentions = await _run_the_journey(
        real_smac_server,
        monkeypatch,
        tmp_path,
        brain_factory=_real_brain,
        trigger_suffix=(
            "reply with exactly the single word pong, nothing else, no punctuation."
        ),
    )

    agent_messages = [m for m in messages if m["Sender"]["member_name"] == "Analyst"]
    assert len(agent_messages) == 1, messages
    assert agent_messages[0]["Message"]["message_text"].strip()

    assert pending_mentions == []
