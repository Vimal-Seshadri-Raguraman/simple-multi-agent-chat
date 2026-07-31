from tests.conftest import human_headers


def test_register_agent_returns_api_key(client):
    response = client.post(
        "/members/agents",
        json={"member_name": "Research-Bot"},
        headers=human_headers("m_1"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["member_type"] == "agent"
    assert body["member_name"] == "Research-Bot"
    assert "api_key" in body and len(body["api_key"]) > 10


def test_register_bot_app_returns_api_key(client):
    response = client.post(
        "/members/bots", json={"member_name": "Zapier"}, headers=human_headers("m_1")
    )
    assert response.status_code == 200
    assert response.json()["member_type"] == "bot_app"


def test_search_members_by_name(client):
    agent1 = client.post(
        "/members/agents",
        json={"member_name": "Research-Bot"},
        headers=human_headers("m_1"),
    ).json()
    client.post(
        "/members/bots", json={"member_name": "Zapier"}, headers=human_headers("m_1")
    )

    headers = {"X-API-Key": agent1["api_key"]}
    response = client.get(
        "/members", params={"search_name": "Research"}, headers=headers
    )
    results = response.json()
    assert len(results) == 1
    assert results[0]["member_name"] == "Research-Bot"


def test_search_members_by_type(client):
    agent1 = client.post(
        "/members/agents", json={"member_name": "Agent-1"}, headers=human_headers("m_1")
    ).json()
    client.post(
        "/members/bots", json={"member_name": "Bot-1"}, headers=human_headers("m_1")
    )

    headers = {"X-API-Key": agent1["api_key"]}
    response = client.get("/members", params={"search_type": "agent"}, headers=headers)
    results = response.json()
    assert len(results) == 1
    assert results[0]["member_type"] == "agent"


def test_search_members_returns_empty_list_when_no_match(client):
    agent1 = client.post(
        "/members/agents", json={"member_name": "Agent-1"}, headers=human_headers("m_1")
    ).json()

    headers = {"X-API-Key": agent1["api_key"]}
    response = client.get("/members", params={"search_name": "nobody"}, headers=headers)
    assert response.status_code == 200
    assert response.json() == []


def test_get_member_profile(client):
    registered = client.post(
        "/members/agents",
        json={"member_name": "Research-Bot"},
        headers=human_headers("m_1"),
    ).json()

    headers = {"X-API-Key": registered["api_key"]}
    response = client.get(
        "/member", params={"id": registered["member_id"]}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["member_id"] == registered["member_id"]
    assert body["member_type"] == "agent"


def test_get_member_profile_404(client):
    agent1 = client.post(
        "/members/agents", json={"member_name": "Agent-1"}, headers=human_headers("m_1")
    ).json()

    headers = {"X-API-Key": agent1["api_key"]}
    response = client.get("/member", params={"id": "does-not-exist"}, headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_search_members_requires_auth(client):
    response = client.get("/members")
    assert response.status_code == 401


def test_get_member_requires_auth(client):
    registered = client.post(
        "/members/agents", json={"member_name": "Bot"}, headers=human_headers("m_1")
    ).json()
    response = client.get("/member", params={"id": registered["member_id"]})
    assert response.status_code == 401
