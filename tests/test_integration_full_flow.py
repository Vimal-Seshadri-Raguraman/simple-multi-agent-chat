from tests.conftest import human_headers


def test_full_flow_human_agent_bot_app_all_post_and_are_visible(client):
    # Human creates a workspace and a channel
    workspace = client.post(
        "/workspaces",
        json={"workspace_name": "Acme"},
        headers=human_headers("m_1", "Alice"),
    ).json()
    channel = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=human_headers("m_1", "Alice"),
    ).json()

    # Register an agent and a bot_app
    agent = client.post("/members/agents", json={"member_name": "Research-Bot"}).json()
    bot = client.post("/members/bots", json={"member_name": "Zapier"}).json()

    # Add the human, agent, and bot_app to the workspace, then the channel
    for member_id in ("m_1", agent["member_id"], bot["member_id"]):
        client.post(
            f"/workspaces/{workspace['workspace_id']}/members",
            json={"member_id": member_id},
            headers=human_headers("m_1", "Alice"),
        )
        client.post(
            f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/members",
            json={"member_id": member_id},
            headers=human_headers("m_1", "Alice"),
        )

    # Each member type posts one message
    client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        json={"message_text": "hi from human"},
        headers=human_headers("m_1", "Alice"),
    )
    client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        json={"message_text": "hi from agent"},
        headers={"X-API-Key": agent["api_key"]},
    )
    client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        json={"message_text": "hi from bot"},
        headers={"X-API-Key": bot["api_key"]},
    )

    # Fetch history and verify all three show up in the agreed wire schema
    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        params={"limit": 15},
        headers=human_headers("m_1", "Alice"),
    )
    assert response.status_code == 200
    messages = response.json()
    assert [m["Message"]["message_text"] for m in messages] == [
        "hi from human",
        "hi from agent",
        "hi from bot",
    ]
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
