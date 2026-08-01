from datetime import datetime

from tests.conftest import human_headers


def _setup_channel_with_agent(client):
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
    agent = client.post(
        "/members/agents",
        json={"member_name": "Bot"},
        headers=human_headers(client, "m_1"),
    ).json()
    client.post(
        f"/workspaces/{workspace['workspace_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=human_headers(client, "m_1"),
    )
    client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=human_headers(client, "m_1"),
    )
    return workspace, channel, agent


def test_channel_member_can_post_message(client):
    workspace, channel, agent = _setup_channel_with_agent(client)
    response = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        json={"message_text": "hello"},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["Message"]["message_text"] == "hello"
    assert body["Sender"]["member_id"] == agent["member_id"]
    assert body["workspace"]["workspace_id"] == workspace["workspace_id"]
    assert body["Channel"]["channel_id"] == channel["channel_id"]


def test_message_timestamp_round_trips_with_utc_offset(client):
    # Regression test for a bug where SQLite discarded tzinfo on write, so a
    # timestamp read back from the DB (as opposed to an in-memory object) came
    # back naive and `.isoformat()` emitted no UTC offset at all.
    workspace, channel, agent = _setup_channel_with_agent(client)
    response = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        json={"message_text": "hello"},
        headers={"X-API-Key": agent["api_key"]},
    )
    timestamp = response.json()["timestamp"]
    assert timestamp.endswith("+00:00")
    parsed = datetime.fromisoformat(timestamp)
    assert parsed.tzinfo is not None


def test_non_channel_member_cannot_post(client):
    workspace, channel, _ = _setup_channel_with_agent(client)
    outsider = client.post(
        "/members/agents",
        json={"member_name": "Outsider"},
        headers=human_headers(client, "m_1"),
    ).json()
    response = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        json={"message_text": "hello"},
        headers={"X-API-Key": outsider["api_key"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_a_member"


def test_empty_message_text_rejected(client):
    workspace, channel, agent = _setup_channel_with_agent(client)
    response = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        json={"message_text": ""},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert response.status_code == 422


def test_get_messages_default_limit_is_5(client):
    workspace, channel, agent = _setup_channel_with_agent(client)
    for i in range(7):
        client.post(
            f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
            json={"message_text": f"msg {i}"},
            headers={"X-API-Key": agent["api_key"]},
        )

    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        headers={"X-API-Key": agent["api_key"]},
    )
    assert response.status_code == 200
    assert len(response.json()) == 5


def test_get_messages_limit_clamped_to_15(client):
    workspace, channel, agent = _setup_channel_with_agent(client)
    for i in range(20):
        client.post(
            f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
            json={"message_text": f"msg {i}"},
            headers={"X-API-Key": agent["api_key"]},
        )

    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        params={"limit": 1000},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert len(response.json()) == 15


def test_get_messages_negative_limit_rejected(client):
    workspace, channel, agent = _setup_channel_with_agent(client)
    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        params={"limit": -1},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert response.status_code == 422


def test_get_messages_zero_limit_rejected(client):
    workspace, channel, agent = _setup_channel_with_agent(client)
    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        params={"limit": 0},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert response.status_code == 422


def test_get_messages_pagination_with_after(client):
    workspace, channel, agent = _setup_channel_with_agent(client)
    posted = []
    for i in range(10):
        resp = client.post(
            f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
            json={"message_text": f"msg {i}"},
            headers={"X-API-Key": agent["api_key"]},
        )
        posted.append(resp.json())

    first_page = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        headers={"X-API-Key": agent["api_key"]},
    ).json()
    assert [m["Message"]["message_text"] for m in first_page] == [
        "msg 0",
        "msg 1",
        "msg 2",
        "msg 3",
        "msg 4",
    ]

    last_id = first_page[-1]["Message"]["message_id"]
    second_page = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        params={"after": last_id},
        headers={"X-API-Key": agent["api_key"]},
    ).json()
    assert [m["Message"]["message_text"] for m in second_page] == [
        "msg 5",
        "msg 6",
        "msg 7",
        "msg 8",
        "msg 9",
    ]


def test_get_messages_pagination_with_after_from_different_channel(client):
    workspace, channel1, agent1 = _setup_channel_with_agent(client)
    # Create a second channel in the same workspace
    channel2 = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels",
        json={"channel_name": "other"},
        headers=human_headers(client, "m_1"),
    ).json()
    agent2 = client.post(
        "/members/agents",
        json={"member_name": "Bot2"},
        headers=human_headers(client, "m_1"),
    ).json()
    client.post(
        f"/workspaces/{workspace['workspace_id']}/members",
        json={"member_id": agent2["member_id"]},
        headers=human_headers(client, "m_1"),
    )
    client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel2['channel_id']}/members",
        json={"member_id": agent2["member_id"]},
        headers=human_headers(client, "m_1"),
    )

    # Post message in channel1
    msg1 = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel1['channel_id']}/messages",
        json={"message_text": "msg1"},
        headers={"X-API-Key": agent1["api_key"]},
    ).json()

    # Post message in channel2
    msg2 = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel2['channel_id']}/messages",
        json={"message_text": "msg2"},
        headers={"X-API-Key": agent2["api_key"]},
    ).json()

    # Try to use channel2's message as anchor in channel1's fetch — should 404
    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel1['channel_id']}/messages",
        params={"after": msg2["Message"]["message_id"]},
        headers={"X-API-Key": agent1["api_key"]},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_get_messages_requires_auth(client):
    workspace, channel, agent = _setup_channel_with_agent(client)
    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages"
    )
    assert response.status_code == 401


def test_get_messages_requires_channel_membership(client):
    workspace, channel, _ = _setup_channel_with_agent(client)
    outsider = client.post(
        "/members/agents",
        json={"member_name": "Outsider"},
        headers=human_headers(client, "m_1"),
    ).json()
    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        headers={"X-API-Key": outsider["api_key"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_a_member"
