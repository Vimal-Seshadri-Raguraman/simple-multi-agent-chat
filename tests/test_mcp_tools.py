"""The 8-tool member surface: happy paths + the error mappings each teaches."""

import asyncio
import json

import pytest

from app import rate_limit as rate_limit_module
from smac_mcp.api import SmacApiError
from smac_mcp.server import build_server
from tests.conftest import founder_auth, founder_headers, general_channel_id
from tests.test_mcp_api import _api_for


def _tool(server, name):
    """The raw async function behind a registered tool (bypasses the MCP
    protocol's error->isError wrapping so tests can assert on SmacApiError
    directly, per the brief: exercise the same code an MCP client would)."""
    return server._tool_manager.get_tool(name).fn


def _add_to_channel(client, workspace_id, channel_id, member_id, workspace_key="w1"):
    response = client.post(
        f"/workspaces/{workspace_id}/channels/{channel_id}/members",
        json={"member_id": member_id},
        headers=founder_headers(client, workspace_key),
    )
    assert response.status_code == 200, response.text


def _agent(client, name="Bridge Bot", workspace_key="w1"):
    return client.post(
        "/members/agents",
        json={"member_name": name},
        headers=founder_headers(client, workspace_key),
    ).json()


def test_whoami_returns_own_profile(client):
    founder_auth(client, "w1")
    agent = _agent(client)
    api = _api_for(client, api_key=agent["api_key"])
    server = build_server(api)

    result = json.loads(asyncio.run(_tool(server, "whoami")()))

    assert result["handle"] == agent["handle"]
    assert result["member_name"] == "Bridge Bot"


def test_catch_me_up_reflects_channel_membership(client):
    founder = founder_auth(client, "w1")
    agent = _agent(client)
    general_id = general_channel_id(client, "w1")
    _add_to_channel(client, founder["workspace_id"], general_id, agent["member_id"])
    api = _api_for(client, api_key=agent["api_key"])
    server = build_server(api)

    result = json.loads(asyncio.run(_tool(server, "catch_me_up")()))

    assert result["unreads"][0]["channel_name"] == "general"
    assert result["unreads"][0]["unread_count"] == 0


def test_list_channels_workspace_scoped(client):
    founder_auth(client, "w1")
    agent = _agent(client)
    api = _api_for(client, api_key=agent["api_key"])
    server = build_server(api)

    result = json.loads(asyncio.run(_tool(server, "list_channels")()))

    names = {c["channel_name"] for c in result}
    assert "general" in names


def test_post_message_then_read_and_check_mentions(client):
    founder = founder_auth(client, "w1")
    agent = _agent(client)
    general_id = general_channel_id(client, "w1")
    _add_to_channel(client, founder["workspace_id"], general_id, agent["member_id"])
    api = _api_for(client, api_key=agent["api_key"])
    server = build_server(api)

    posted = json.loads(
        asyncio.run(
            _tool(server, "post_message")(channel_id=general_id, text="hi @tw1")
        )
    )
    assert posted["Message"]["message_text"].startswith("hi ")
    assert posted["mentions"][0]["handle"] == "tw1"

    read_back = json.loads(
        asyncio.run(_tool(server, "read_messages")(channel_id=general_id))
    )
    assert len(read_back) == 1
    assert read_back[0]["Message"]["message_id"] == posted["Message"]["message_id"]

    # reading never marks read: posting your own message DOES advance your
    # own cursor server-side though, so assert on unread_count staying 0
    # (the invariant we can observe) rather than "nothing changed at all".
    caught_up = json.loads(asyncio.run(_tool(server, "catch_me_up")()))
    assert caught_up["unreads"][0]["unread_count"] == 0


def test_ack_mention_after_check_mentions(client):
    founder = founder_auth(client, "w1")
    agent = _agent(client)
    general_id = general_channel_id(client, "w1")
    _add_to_channel(client, founder["workspace_id"], general_id, agent["member_id"])
    client.post(
        f"/workspaces/{founder['workspace_id']}/channels/{general_id}/messages",
        json={"message_text": f"@{agent['handle']} ping"},
        headers=founder_headers(client, "w1"),
    )
    api = _api_for(client, api_key=agent["api_key"])
    server = build_server(api)

    mentions = json.loads(asyncio.run(_tool(server, "check_mentions")()))
    assert len(mentions) == 1
    assert mentions[0]["event"] == "mention"
    mention_id = mentions[0]["mention_id"]

    ack_result = json.loads(
        asyncio.run(_tool(server, "ack_mention")(mention_id=mention_id))
    )
    assert ack_result == {"status": "acknowledged"}

    mentions_after = json.loads(asyncio.run(_tool(server, "check_mentions")()))
    assert mentions_after == []


def test_mark_read_zeroes_unread_count(client):
    founder = founder_auth(client, "w1")
    agent = _agent(client)
    general_id = general_channel_id(client, "w1")
    _add_to_channel(client, founder["workspace_id"], general_id, agent["member_id"])
    client.post(
        f"/workspaces/{founder['workspace_id']}/channels/{general_id}/messages",
        json={"message_text": "hello there"},
        headers=founder_headers(client, "w1"),
    )
    api = _api_for(client, api_key=agent["api_key"])
    server = build_server(api)

    before = json.loads(asyncio.run(_tool(server, "catch_me_up")()))
    assert before["unreads"][0]["unread_count"] == 1

    marked = json.loads(asyncio.run(_tool(server, "mark_read")(channel_id=general_id)))
    assert marked["unread_count"] == 0

    after = json.loads(asyncio.run(_tool(server, "catch_me_up")()))
    assert after["unreads"][0]["unread_count"] == 0


# --- error cases -----------------------------------------------------------


def test_post_message_to_foreign_channel_is_403_passthrough(client):
    """Channel exists in the caller's own workspace, but the agent was
    never added to it: authorize_post_message's 403 not_a_member,
    passed through verbatim so the LLM learns to ask a human for access."""
    founder_auth(client, "w1")
    agent = _agent(client)
    general_id = general_channel_id(client, "w1")
    # agent is NOT added to the channel
    api = _api_for(client, api_key=agent["api_key"])
    server = build_server(api)

    with pytest.raises(SmacApiError) as exc_info:
        asyncio.run(_tool(server, "post_message")(channel_id=general_id, text="hi"))
    assert "not a member" in str(exc_info.value).lower()


def test_ack_mention_foreign_mention_is_uniform_404(client):
    founder = founder_auth(client, "w1")
    agent = _agent(client)
    other_agent = _agent(client, name="Other Bot")
    general_id = general_channel_id(client, "w1")
    for a in (agent, other_agent):
        _add_to_channel(client, founder["workspace_id"], general_id, a["member_id"])
    client.post(
        f"/workspaces/{founder['workspace_id']}/channels/{general_id}/messages",
        json={"message_text": f"@{other_agent['handle']} ping"},
        headers=founder_headers(client, "w1"),
    )
    other_api = _api_for(client, api_key=other_agent["api_key"])
    other_server = build_server(other_api)
    foreign_mention_id = json.loads(
        asyncio.run(_tool(other_server, "check_mentions")())
    )[0]["mention_id"]

    api = _api_for(client, api_key=agent["api_key"])
    server = build_server(api)
    with pytest.raises(SmacApiError) as exc_info:
        asyncio.run(_tool(server, "ack_mention")(mention_id=foreign_mention_id))
    assert "not found" in str(exc_info.value).lower()


def test_read_messages_foreign_channel_is_404(client):
    """A channel_id from a DIFFERENT workspace: the wall's uniform 404,
    indistinguishable from a nonexistent id (contrast with a channel that
    exists in your own workspace but you're merely not a member of, which
    is the 403 not_a_member case covered by the post_message test above)."""
    founder_auth(client, "w1")
    agent = _agent(client)
    foreign_channel_id = general_channel_id(client, "w2")
    api = _api_for(client, api_key=agent["api_key"])
    server = build_server(api)

    with pytest.raises(SmacApiError) as exc_info:
        asyncio.run(_tool(server, "read_messages")(channel_id=foreign_channel_id))
    assert "not found" in str(exc_info.value).lower()


def test_post_message_rate_limit_passthrough(client, monkeypatch):
    small_limiter = rate_limit_module.SlidingWindowRateLimiter(
        max_events=1, window_seconds=60
    )
    monkeypatch.setattr(rate_limit_module, "post_limiter", small_limiter)

    founder = founder_auth(client, "w1")
    agent = _agent(client)
    general_id = general_channel_id(client, "w1")
    _add_to_channel(client, founder["workspace_id"], general_id, agent["member_id"])
    api = _api_for(client, api_key=agent["api_key"])
    server = build_server(api)

    first = asyncio.run(
        _tool(server, "post_message")(channel_id=general_id, text="one")
    )
    assert json.loads(first)["Message"]["message_text"] == "one"

    with pytest.raises(SmacApiError) as exc_info:
        asyncio.run(_tool(server, "post_message")(channel_id=general_id, text="two"))
    assert "posting too fast" in str(exc_info.value).lower()


def test_raised_error_surfaces_as_mcp_tool_error_not_a_crash(client):
    """Verifies FastMCP's contract (mcp.server.lowlevel.server.Server.call_tool):
    a raised exception from a tool becomes CallToolResult(isError=True,
    content=[TextContent(text=str(e))]) rather than propagating out of / crashing
    the bridge process. FastMCP.call_tool (used here, one layer up from the
    stdio-facing lowlevel handler) wraps it as ToolError first, whose message
    embeds the original SmacApiError text -- confirming raised SmacApiErrors
    are never swallowed silently, only converted to an error result."""
    from mcp.server.fastmcp.exceptions import ToolError

    founder_auth(client, "w1")
    agent = _agent(client)
    general_id = general_channel_id(client, "w1")
    # agent is NOT added to the channel -> SmacApiError("... not a member ...")
    api = _api_for(client, api_key=agent["api_key"])
    server = build_server(api)

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(
            server.call_tool("post_message", {"channel_id": general_id, "text": "hi"})
        )
    assert "not a member" in str(exc_info.value).lower()
