from tests.conftest import human_headers, human_member_id


def test_full_flow_human_agent_bot_app_all_post_and_are_visible(client):
    # Human creates a workspace and a channel
    workspace = client.post(
        "/workspaces",
        json={"workspace_name": "Acme"},
        headers=human_headers(client, "m_1"),
    ).json()
    channel = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=human_headers(client, "m_1"),
    ).json()

    # Register an agent and a bot_app
    agent = client.post(
        "/members/agents",
        json={"member_name": "Research-Bot"},
        headers=human_headers(client, "m_1"),
    ).json()
    bot = client.post(
        "/members/bots",
        json={"member_name": "Zapier"},
        headers=human_headers(client, "m_1"),
    ).json()

    # Add the agent and bot_app to the workspace (the human/creator is already
    # a workspace member, auto-added at workspace creation).
    for member_id in (agent["member_id"], bot["member_id"]):
        resp = client.post(
            f"/workspaces/{workspace['workspace_id']}/members",
            json={"member_id": member_id},
            headers=human_headers(client, "m_1"),
        )
        assert resp.status_code == 200

    # Add the human, agent, and bot_app to the channel — a channel created via
    # the channels endpoint, not the workspace's auto-created default, so
    # nobody is auto-joined to it.
    for member_id in (
        human_member_id(client, "m_1"),
        agent["member_id"],
        bot["member_id"],
    ):
        resp = client.post(
            f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/members",
            json={"member_id": member_id},
            headers=human_headers(client, "m_1"),
        )
        assert resp.status_code == 200

    # Each member type posts one message
    resp = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        json={"message_text": "hi from human"},
        headers=human_headers(client, "m_1"),
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        json={"message_text": "hi from agent"},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        json={"message_text": "hi from bot"},
        headers={"X-API-Key": bot["api_key"]},
    )
    assert resp.status_code == 200

    # Fetch history and verify all three show up in the agreed wire schema
    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        params={"limit": 15},
        headers=human_headers(client, "m_1"),
    )
    assert response.status_code == 200
    messages = response.json()
    assert [m["Message"]["message_text"] for m in messages] == [
        "hi from human",
        "hi from agent",
        "hi from bot",
    ]
    # Verify each message is attributed to the correct sender
    assert messages[0]["Sender"]["member_id"] == human_member_id(client, "m_1")
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
        assert message["workspace"]["workspace_id"] == workspace["workspace_id"]
        assert message["Channel"]["channel_id"] == channel["channel_id"]
