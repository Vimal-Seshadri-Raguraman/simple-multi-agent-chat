"""E2E: Claude Desktop's view — summoned by mention, replies, cleans up."""

import asyncio
import json

from smac_mcp.server import build_server
from tests.conftest import founder_auth, founder_headers, general_channel_id
from tests.test_mcp_api import _api_for


def _tool(server, name):
    return server._tool_manager.get_tool(name).fn


def test_claude_is_summoned_replies_and_cleans_up(client):
    founder = founder_auth(client, "w1")
    general_id = general_channel_id(client, "w1")

    # Founder creates agent "Claude" and adds it to #general.
    claude = client.post(
        "/members/agents",
        json={"member_name": "Claude"},
        headers=founder_headers(client, "w1"),
    ).json()
    assert claude["handle"] == "claude"
    client.post(
        f"/workspaces/{founder['workspace_id']}/channels/{general_id}/members",
        json={"member_id": claude["member_id"]},
        headers=founder_headers(client, "w1"),
    )

    api = _api_for(client, api_key=claude["api_key"])
    server = build_server(api)

    # whoami is the first thing Claude Desktop checks in a session.
    who = json.loads(asyncio.run(_tool(server, "whoami")()))
    assert who["handle"] == "claude"

    # Founder mentions @claude.
    founder_post = client.post(
        f"/workspaces/{founder['workspace_id']}/channels/{general_id}/messages",
        json={"message_text": "@claude what's our runway?"},
        headers=founder_headers(client, "w1"),
    ).json()

    # The bridge sees exactly one pending mention, carrying the triggering message.
    mentions = json.loads(asyncio.run(_tool(server, "check_mentions")()))
    assert len(mentions) == 1
    event = mentions[0]
    assert event["event"] == "mention"
    assert f"<@{claude['member_id']}>" in event["message"]["Message"]["message_text"]
    assert (
        event["message"]["Message"]["message_id"]
        == founder_post["Message"]["message_id"]
    )
    mention_id = event["mention_id"]

    # Claude replies, mentioning the founder back by handle.
    reply = json.loads(
        asyncio.run(
            _tool(server, "post_message")(
                channel_id=general_id, text="@tw1 about 14 months"
            )
        )
    )
    assert (
        reply["Message"]["message_text"]
        == "<@" + founder["member_id"] + "> about 14 months"
    )
    assert reply["mentions"] == [
        {
            "member_id": founder["member_id"],
            "handle": "tw1",
            "member_name": "Test w1",
        }
    ]

    # Having handled it, Claude acks the mention.
    ack = json.loads(asyncio.run(_tool(server, "ack_mention")(mention_id=mention_id)))
    assert ack == {"status": "acknowledged"}

    # The inbox is now empty.
    assert json.loads(asyncio.run(_tool(server, "check_mentions")())) == []

    # catch_me_up reflects reality: Claude has read up through its own reply
    # (posting advances your own cursor), so general is caught up already.
    caught_up = json.loads(asyncio.run(_tool(server, "catch_me_up")()))
    general_row = [r for r in caught_up["unreads"] if r["channel_name"] == "general"][0]
    assert general_row["unread_count"] == 0
    assert general_row["mention_count"] == 0

    # mark_read is still safe/idempotent to call explicitly to close out the session.
    marked = json.loads(asyncio.run(_tool(server, "mark_read")(channel_id=general_id)))
    assert marked["unread_count"] == 0
