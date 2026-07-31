from tests.conftest import human_headers


def _create_workspace(client, member_id="m_1"):
    return client.post(
        "/workspaces", json={"workspace_name": "Acme"}, headers=human_headers(member_id)
    ).json()


def test_human_can_create_channel(client):
    workspace = _create_workspace(client)
    response = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=human_headers("m_1"),
    )
    assert response.status_code == 200
    assert response.json()["channel_name"] == "general"


def test_bot_app_cannot_create_channel(client):
    workspace = _create_workspace(client)
    bot = client.post("/members/bots", json={"member_name": "Zapier"}).json()
    response = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers={"X-API-Key": bot["api_key"]},
    )
    assert response.status_code == 403


def test_create_channel_in_nonexistent_workspace_404s(client):
    response = client.post(
        "/workspaces/does-not-exist/channels",
        json={"channel_name": "general"},
        headers=human_headers("m_1"),
    )
    assert response.status_code == 404


def test_list_channels(client):
    workspace = _create_workspace(client)
    # Add creator to workspace members
    client.post(
        f"/workspaces/{workspace['workspace_id']}/members",
        json={"member_id": "m_1"},
        headers=human_headers("m_1"),
    )
    client.post(
        f"/workspaces/{workspace['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=human_headers("m_1"),
    )
    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels",
        headers=human_headers("m_1"),
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_list_channels_requires_auth(client):
    workspace = _create_workspace(client)
    response = client.get(f"/workspaces/{workspace['workspace_id']}/channels")
    assert response.status_code == 401


def test_list_channels_requires_workspace_membership(client):
    workspace = _create_workspace(client)
    outsider_agent = client.post(
        "/members/agents", json={"member_name": "Outsider"}
    ).json()
    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels",
        headers={"X-API-Key": outsider_agent["api_key"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_a_member"


def test_add_channel_member_requires_workspace_membership_first(client):
    workspace = _create_workspace(client)
    channel = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=human_headers("m_1"),
    ).json()
    agent = client.post("/members/agents", json={"member_name": "Bot"}).json()

    response = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=human_headers("m_1"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_a_workspace_member"


def test_add_channel_member_succeeds_once_in_workspace(client):
    workspace = _create_workspace(client)
    channel = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=human_headers("m_1"),
    ).json()
    agent = client.post("/members/agents", json={"member_name": "Bot"}).json()
    client.post(
        f"/workspaces/{workspace['workspace_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=human_headers("m_1"),
    )

    response = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=human_headers("m_1"),
    )
    assert response.status_code == 200


def test_list_channel_members(client):
    workspace = _create_workspace(client)
    # Add creator to workspace members
    client.post(
        f"/workspaces/{workspace['workspace_id']}/members",
        json={"member_id": "m_1"},
        headers=human_headers("m_1"),
    )
    channel = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=human_headers("m_1"),
    ).json()
    agent = client.post("/members/agents", json={"member_name": "Bot"}).json()
    client.post(
        f"/workspaces/{workspace['workspace_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=human_headers("m_1"),
    )
    # Add creator to channel members
    client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/members",
        json={"member_id": "m_1"},
        headers=human_headers("m_1"),
    )
    client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=human_headers("m_1"),
    )

    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/members",
        headers=human_headers("m_1"),
    )
    assert response.status_code == 200
    member_ids = [m["member_id"] for m in response.json()]
    assert agent["member_id"] in member_ids


def test_list_channel_members_requires_auth(client):
    workspace = _create_workspace(client)
    channel = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=human_headers("m_1"),
    ).json()
    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/members"
    )
    assert response.status_code == 401


def test_list_channel_members_requires_channel_membership(client):
    workspace = _create_workspace(client)
    channel = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=human_headers("m_1"),
    ).json()
    outsider_agent = client.post(
        "/members/agents", json={"member_name": "Outsider"}
    ).json()
    # Add outsider to workspace but not to channel
    client.post(
        f"/workspaces/{workspace['workspace_id']}/members",
        json={"member_id": outsider_agent["member_id"]},
        headers=human_headers("m_1"),
    )
    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/members",
        headers={"X-API-Key": outsider_agent["api_key"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_a_member"
