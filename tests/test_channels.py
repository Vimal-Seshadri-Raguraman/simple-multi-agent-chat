from tests.conftest import founder_auth, founder_headers


def test_human_can_create_channel(client):
    founder = founder_auth(client, "w1")
    response = client.post(
        f"/workspaces/{founder['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200
    assert response.json()["channel_name"] == "general"


def test_bot_app_cannot_create_channel(client):
    founder = founder_auth(client, "w1")
    bot = client.post(
        "/members/bots",
        json={"member_name": "Zapier"},
        headers=founder_headers(client, "w1"),
    ).json()
    response = client.post(
        f"/workspaces/{founder['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers={"X-API-Key": bot["api_key"]},
    )
    assert response.status_code == 403


def test_create_channel_in_foreign_workspace_404s(client):
    founder_auth(client, "w1")
    response = client.post(
        "/workspaces/does-not-exist/channels",
        json={"channel_name": "general"},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 404


def test_list_channels(client):
    founder = founder_auth(client, "w1")
    # Founding already bootstraps a "general" channel; create one more.
    client.post(
        f"/workspaces/{founder['workspace_id']}/channels",
        json={"channel_name": "random"},
        headers=founder_headers(client, "w1"),
    )
    response = client.get(
        f"/workspaces/{founder['workspace_id']}/channels",
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_list_channels_requires_auth(client):
    founder = founder_auth(client, "w1")
    response = client.get(f"/workspaces/{founder['workspace_id']}/channels")
    assert response.status_code == 401


def test_list_channels_requires_same_workspace(client):
    founder = founder_auth(client, "w1")
    response = client.get(
        f"/workspaces/{founder['workspace_id']}/channels",
        headers=founder_headers(client, "w2"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_add_channel_member_requires_workspace_membership_first(client):
    founder = founder_auth(client, "w1")
    channel = client.post(
        f"/workspaces/{founder['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=founder_headers(client, "w1"),
    ).json()
    outsider = founder_auth(client, "w2")

    response = client.post(
        f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/members",
        json={"member_id": outsider["member_id"]},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_a_workspace_member"


def test_add_channel_member_succeeds_once_in_workspace(client):
    founder = founder_auth(client, "w1")
    channel = client.post(
        f"/workspaces/{founder['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=founder_headers(client, "w1"),
    ).json()
    agent = client.post(
        "/members/agents",
        json={"member_name": "Bot"},
        headers=founder_headers(client, "w1"),
    ).json()

    response = client.post(
        f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200


def test_bot_app_cannot_add_channel_member(client):
    founder = founder_auth(client, "w1")
    channel = client.post(
        f"/workspaces/{founder['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=founder_headers(client, "w1"),
    ).json()
    bot = client.post(
        "/members/bots",
        json={"member_name": "Zapier"},
        headers=founder_headers(client, "w1"),
    ).json()
    agent = client.post(
        "/members/agents",
        json={"member_name": "Bot"},
        headers=founder_headers(client, "w1"),
    ).json()

    response = client.post(
        f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/members",
        json={"member_id": agent["member_id"]},
        headers={"X-API-Key": bot["api_key"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden_member_type"


def test_adding_same_channel_member_twice_conflicts(client):
    founder = founder_auth(client, "w1")
    channel = client.post(
        f"/workspaces/{founder['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=founder_headers(client, "w1"),
    ).json()
    agent = client.post(
        "/members/agents",
        json={"member_name": "Bot"},
        headers=founder_headers(client, "w1"),
    ).json()
    add_url = f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/members"
    client.post(
        add_url,
        json={"member_id": agent["member_id"]},
        headers=founder_headers(client, "w1"),
    )

    response = client.post(
        add_url,
        json={"member_id": agent["member_id"]},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_a_member"


def test_list_channel_members(client):
    founder = founder_auth(client, "w1")
    channel = client.post(
        f"/workspaces/{founder['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=founder_headers(client, "w1"),
    ).json()
    agent = client.post(
        "/members/agents",
        json={"member_name": "Bot"},
        headers=founder_headers(client, "w1"),
    ).json()
    # Add founder to channel members
    client.post(
        f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/members",
        json={"member_id": founder["member_id"]},
        headers=founder_headers(client, "w1"),
    )
    client.post(
        f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=founder_headers(client, "w1"),
    )

    response = client.get(
        f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/members",
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200
    member_ids = [m["member_id"] for m in response.json()]
    assert agent["member_id"] in member_ids


def test_list_channel_members_requires_auth(client):
    founder = founder_auth(client, "w1")
    channel = client.post(
        f"/workspaces/{founder['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=founder_headers(client, "w1"),
    ).json()
    response = client.get(
        f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/members"
    )
    assert response.status_code == 401


def test_list_channel_members_requires_channel_membership(client):
    founder = founder_auth(client, "w1")
    channel = client.post(
        f"/workspaces/{founder['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=founder_headers(client, "w1"),
    ).json()
    # Outsider agent belongs to the same workspace (registered by its founder)
    # but is not in this specific channel.
    outsider_agent = client.post(
        "/members/agents",
        json={"member_name": "Outsider"},
        headers=founder_headers(client, "w1"),
    ).json()
    response = client.get(
        f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/members",
        headers={"X-API-Key": outsider_agent["api_key"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_a_member"
