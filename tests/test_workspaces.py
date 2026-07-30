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
    register = client.post("/members/agents", json={"member_name": "Bot"}).json()
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
    response = client.get("/workspaces")
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_add_member_to_workspace(client):
    workspace = client.post(
        "/workspaces", json={"workspace_name": "Acme"}, headers=human_headers("m_1")
    ).json()
    agent = client.post("/members/agents", json={"member_name": "Bot"}).json()

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
    agent = client.post("/members/agents", json={"member_name": "Bot"}).json()
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
    agent = client.post("/members/agents", json={"member_name": "Bot"}).json()
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
    agent = client.post("/members/agents", json={"member_name": "Bot"}).json()
    client.post(
        f"/workspaces/{workspace['workspace_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=human_headers("m_1"),
    )

    response = client.get(f"/workspaces/{workspace['workspace_id']}/members")
    assert response.status_code == 200
    member_ids = [m["member_id"] for m in response.json()]
    assert agent["member_id"] in member_ids
