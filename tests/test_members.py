def test_register_agent_returns_api_key(client):
    response = client.post("/members/agents", json={"member_name": "Research-Bot"})
    assert response.status_code == 200
    body = response.json()
    assert body["member_type"] == "agent"
    assert body["member_name"] == "Research-Bot"
    assert "api_key" in body and len(body["api_key"]) > 10


def test_register_bot_app_returns_api_key(client):
    response = client.post("/members/bots", json={"member_name": "Zapier"})
    assert response.status_code == 200
    assert response.json()["member_type"] == "bot_app"


def test_search_members_by_name(client):
    client.post("/members/agents", json={"member_name": "Research-Bot"})
    client.post("/members/bots", json={"member_name": "Zapier"})

    response = client.get("/members", params={"search_name": "Research"})
    results = response.json()
    assert len(results) == 1
    assert results[0]["member_name"] == "Research-Bot"


def test_search_members_by_type(client):
    client.post("/members/agents", json={"member_name": "Agent-1"})
    client.post("/members/bots", json={"member_name": "Bot-1"})

    response = client.get("/members", params={"search_type": "agent"})
    results = response.json()
    assert len(results) == 1
    assert results[0]["member_type"] == "agent"


def test_search_members_returns_empty_list_when_no_match(client):
    response = client.get("/members", params={"search_name": "nobody"})
    assert response.status_code == 200
    assert response.json() == []


def test_get_member_profile(client):
    registered = client.post(
        "/members/agents", json={"member_name": "Research-Bot"}
    ).json()

    response = client.get("/member", params={"id": registered["member_id"]})
    assert response.status_code == 200
    body = response.json()
    assert body["member_id"] == registered["member_id"]
    assert body["member_type"] == "agent"


def test_get_member_profile_404(client):
    response = client.get("/member", params={"id": "does-not-exist"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
