from tests.conftest import founder_auth, founder_headers


def test_full_flow_human_agent_bot_app_all_post_and_are_visible(client):
    # Human founds a workspace (general auto-created) and a second channel
    founder = founder_auth(client, "w1")
    channel = client.post(
        f"/workspaces/{founder['workspace_id']}/channels",
        json={"channel_name": "general2"},
        headers=founder_headers(client, "w1"),
    ).json()

    # Register an agent and a bot_app (workspace-scoped to the founder's workspace)
    agent = client.post(
        "/members/agents",
        json={"member_name": "Research-Bot"},
        headers=founder_headers(client, "w1"),
    ).json()
    bot = client.post(
        "/members/bots",
        json={"member_name": "Zapier"},
        headers=founder_headers(client, "w1"),
    ).json()

    # Add the human, agent, and bot_app to the channel — a channel created via
    # the channels endpoint, not the workspace's auto-created default, so
    # nobody is auto-joined to it.
    for member_id in (founder["member_id"], agent["member_id"], bot["member_id"]):
        resp = client.post(
            f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/members",
            json={"member_id": member_id},
            headers=founder_headers(client, "w1"),
        )
        assert resp.status_code == 200

    # Each member type posts one message
    resp = client.post(
        f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/messages",
        json={"message_text": "hi from human"},
        headers=founder_headers(client, "w1"),
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/messages",
        json={"message_text": "hi from agent"},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/messages",
        json={"message_text": "hi from bot"},
        headers={"X-API-Key": bot["api_key"]},
    )
    assert resp.status_code == 200

    # Fetch history and verify all three show up in the agreed wire schema
    response = client.get(
        f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/messages",
        params={"limit": 15},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200
    messages = response.json()
    assert [m["Message"]["message_text"] for m in messages] == [
        "hi from human",
        "hi from agent",
        "hi from bot",
    ]
    # Verify each message is attributed to the correct sender
    assert messages[0]["Sender"]["member_id"] == founder["member_id"]
    assert messages[1]["Sender"]["member_id"] == agent["member_id"]
    assert messages[2]["Sender"]["member_id"] == bot["member_id"]
    # Verify wire schema and workspace/channel context
    for message in messages:
        assert set(message.keys()) == {
            "timestamp",
            "workspace",
            "Channel",
            "Sender",
            "Message",
        }
        assert message["workspace"]["workspace_id"] == founder["workspace_id"]
        assert message["Channel"]["channel_id"] == channel["channel_id"]
