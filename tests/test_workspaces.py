from tests.conftest import human_headers


def test_human_can_create_workspace(client):
    response = client.post(
        "/workspaces", json={"workspace_name": "Acme"}, headers=human_headers("m_1")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workspace_name"] == "Acme"
    assert "workspace_id" in body


def test_agent_cannot_create_workspace(client):
    register = client.post(
        "/members/agents", json={"member_name": "Bot"}, headers=human_headers("m_1")
    ).json()
    response = client.post(
        "/workspaces",
        json={"workspace_name": "Acme"},
        headers={"X-API-Key": register["api_key"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden_member_type"


def test_list_workspaces(client):
    client.post(
        "/workspaces", json={"workspace_name": "Acme"}, headers=human_headers("m_1")
    )
    response = client.get("/workspaces", headers=human_headers("m_1"))
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_add_member_to_workspace(client):
    workspace = client.post(
        "/workspaces", json={"workspace_name": "Acme"}, headers=human_headers("m_1")
    ).json()
    agent = client.post(
        "/members/agents", json={"member_name": "Bot"}, headers=human_headers("m_1")
    ).json()

    response = client.post(
        f"/workspaces/{workspace['workspace_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=human_headers("m_1"),
    )
    assert response.status_code == 200
    assert response.json()["member_id"] == agent["member_id"]


def test_adding_same_member_twice_conflicts(client):
    workspace = client.post(
        "/workspaces", json={"workspace_name": "Acme"}, headers=human_headers("m_1")
    ).json()
    agent = client.post(
        "/members/agents", json={"member_name": "Bot"}, headers=human_headers("m_1")
    ).json()
    add_url = f"/workspaces/{workspace['workspace_id']}/members"
    client.post(
        add_url, json={"member_id": agent["member_id"]}, headers=human_headers("m_1")
    )

    response = client.post(
        add_url, json={"member_id": agent["member_id"]}, headers=human_headers("m_1")
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_a_member"


def test_add_member_to_nonexistent_workspace_404s(client):
    agent = client.post(
        "/members/agents", json={"member_name": "Bot"}, headers=human_headers("m_1")
    ).json()
    response = client.post(
        "/workspaces/does-not-exist/members",
        json={"member_id": agent["member_id"]},
        headers=human_headers("m_1"),
    )
    assert response.status_code == 404


def test_list_workspace_members(client):
    workspace = client.post(
        "/workspaces", json={"workspace_name": "Acme"}, headers=human_headers("m_1")
    ).json()
    agent = client.post(
        "/members/agents", json={"member_name": "Bot"}, headers=human_headers("m_1")
    ).json()

    # Add creator (m_1) to workspace members
    client.post(
        f"/workspaces/{workspace['workspace_id']}/members",
        json={"member_id": "m_1"},
        headers=human_headers("m_1"),
    )
    # Add agent to workspace members
    client.post(
        f"/workspaces/{workspace['workspace_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=human_headers("m_1"),
    )

    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/members",
        headers=human_headers("m_1"),
    )
    assert response.status_code == 200
    member_ids = [m["member_id"] for m in response.json()]
    assert agent["member_id"] in member_ids
    assert "m_1" in member_ids


def test_list_workspaces_requires_auth(client):
    response = client.get("/workspaces")
    assert response.status_code == 401


def test_list_workspaces_works_for_any_authenticated_member_type(client):
    agent = client.post(
        "/members/agents", json={"member_name": "Bot"}, headers=human_headers("m_1")
    ).json()
    response = client.get("/workspaces", headers={"X-API-Key": agent["api_key"]})
    assert response.status_code == 200


def test_list_workspace_members_requires_auth(client):
    workspace = client.post(
        "/workspaces", json={"workspace_name": "Acme"}, headers=human_headers("m_1")
    ).json()
    response = client.get(f"/workspaces/{workspace['workspace_id']}/members")
    assert response.status_code == 401


def test_list_workspace_members_requires_membership(client):
    workspace = client.post(
        "/workspaces", json={"workspace_name": "Acme"}, headers=human_headers("m_1")
    ).json()
    outsider_agent = client.post(
        "/members/agents",
        json={"member_name": "Outsider"},
        headers=human_headers("m_1"),
    ).json()
    response = client.get(
        f"/workspaces/{workspace['workspace_id']}/members",
        headers={"X-API-Key": outsider_agent["api_key"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_a_member"
