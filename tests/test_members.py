from tests.conftest import (
    founder_auth,
    founder_headers,
    member_auth,
    member_headers,
)


def test_register_agent_returns_api_key(client):
    response = client.post(
        "/members/agents",
        json={"member_name": "Research-Bot"},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["member_type"] == "agent"
    assert body["member_name"] == "Research-Bot"
    assert "api_key" in body and len(body["api_key"]) > 10


def test_register_bot_app_returns_api_key(client):
    response = client.post(
        "/members/bots",
        json={"member_name": "Zapier"},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200
    assert response.json()["member_type"] == "bot_app"


def test_search_members_by_name(client):
    agent1 = client.post(
        "/members/agents",
        json={"member_name": "Research-Bot"},
        headers=founder_headers(client, "w1"),
    ).json()
    client.post(
        "/members/bots",
        json={"member_name": "Zapier"},
        headers=founder_headers(client, "w1"),
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
        "/members/agents",
        json={"member_name": "Agent-1"},
        headers=founder_headers(client, "w1"),
    ).json()
    client.post(
        "/members/bots",
        json={"member_name": "Bot-1"},
        headers=founder_headers(client, "w1"),
    )

    headers = {"X-API-Key": agent1["api_key"]}
    response = client.get("/members", params={"search_type": "agent"}, headers=headers)
    results = response.json()
    assert len(results) == 1
    assert results[0]["member_type"] == "agent"


def test_search_members_returns_empty_list_when_no_match(client):
    agent1 = client.post(
        "/members/agents",
        json={"member_name": "Agent-1"},
        headers=founder_headers(client, "w1"),
    ).json()

    headers = {"X-API-Key": agent1["api_key"]}
    response = client.get("/members", params={"search_name": "nobody"}, headers=headers)
    assert response.status_code == 200
    assert response.json() == []


def test_get_member_profile(client):
    registered = client.post(
        "/members/agents",
        json={"member_name": "Research-Bot"},
        headers=founder_headers(client, "w1"),
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
        "/members/agents",
        json={"member_name": "Agent-1"},
        headers=founder_headers(client, "w1"),
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
        "/members/agents",
        json={"member_name": "Bot"},
        headers=founder_headers(client, "w1"),
    ).json()
    response = client.get("/member", params={"id": registered["member_id"]})
    assert response.status_code == 401


def test_get_own_profile_includes_email(client):
    founder = founder_auth(client, "w1")
    response = client.get(
        "/member",
        params={"id": founder["member_id"]},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "w1@test.example"
    assert body["first_name"] == "Test"


def test_get_other_profile_hides_email(client):
    other = member_auth(client, "m2", "w1")
    response = client.get(
        "/member",
        params={"id": other["member_id"]},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] is None
    assert body["first_name"] == "Test"  # profile fields still visible


def test_search_never_exposes_emails(client):
    founder_auth(client, "w1")
    results = client.get("/members", headers=founder_headers(client, "w1")).json()
    assert all("email" not in row for row in results)


def test_get_my_profile_via_bearer(client):
    founder = founder_auth(client, "w1")
    response = client.get("/members/me", headers=founder_headers(client, "w1"))
    assert response.status_code == 200
    body = response.json()
    assert body["member_id"] == founder["member_id"]
    assert body["workspace_id"] == founder["workspace_id"]
    assert body["email"] == "w1@test.example"


def test_get_my_profile_via_api_key(client):
    agent = client.post(
        "/members/agents",
        json={"member_name": "Research-Bot"},
        headers=founder_headers(client, "w1"),
    ).json()
    response = client.get("/members/me", headers={"X-API-Key": agent["api_key"]})
    assert response.status_code == 200
    body = response.json()
    assert body["member_id"] == agent["member_id"]
    assert body["member_type"] == "agent"
    assert body["workspace_id"] == founder_auth(client, "w1")["workspace_id"]


def test_get_my_profile_requires_auth(client):
    response = client.get("/members/me")
    assert response.status_code == 401


def test_patch_own_profile(client):
    response = client.patch(
        "/members/me",
        json={"company": "Acme", "job_role": "Engineer", "display_name": "Neo"},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["company"] == "Acme"
    assert body["job_role"] == "Engineer"
    assert body["member_name"] == "Neo"
    assert body["first_name"] == "Test"  # untouched fields preserved


def test_patch_cannot_change_email(client):
    response = client.patch(
        "/members/me",
        json={"email": "hacker@evil.com"},
        headers=founder_headers(client, "w1"),
    )
    # Unknown fields are ignored by Pydantic; email must be unchanged.
    assert response.status_code == 200
    assert response.json()["email"] == "w1@test.example"


def test_patch_explicit_null_display_name_is_422(client):
    response = client.patch(
        "/members/me",
        json={"display_name": None},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 422


def test_patch_explicit_null_first_name_is_422(client):
    response = client.patch(
        "/members/me",
        json={"first_name": None},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 422


def test_patch_explicit_null_last_name_is_422(client):
    response = client.patch(
        "/members/me",
        json={"last_name": None},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 422


def test_patch_explicit_null_company_clears_it(client):
    client.patch(
        "/members/me",
        json={"company": "Acme"},
        headers=founder_headers(client, "w1"),
    )
    response = client.patch(
        "/members/me",
        json={"company": None},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200
    assert response.json()["company"] is None


def test_agent_cannot_patch_profile(client):
    agent = client.post(
        "/members/agents",
        json={"member_name": "Bot"},
        headers=founder_headers(client, "w1"),
    ).json()
    response = client.patch(
        "/members/me",
        json={"company": "Acme"},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden_member_type"


def test_human_handle_generated_first_initial_lastname(client):
    auth = founder_auth(client, "w1")  # founder: first_name="Test", last_name="w1"
    me = client.get(
        "/member",
        params={"id": auth["member_id"]},
        headers=founder_headers(client, "w1"),
    ).json()
    assert me["handle"] == "tw1"


def test_agent_handle_from_display_name(client):
    agent = client.post(
        "/members/agents",
        json={"member_name": "Helper Bot!"},
        headers=founder_headers(client, "w1"),
    ).json()
    assert agent["handle"] == "helper-bot"


def test_patch_handle_and_collision(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    r = client.patch(
        "/members/me", json={"handle": "boss"}, headers=founder_headers(client, "w1")
    )
    assert r.status_code == 200 and r.json()["handle"] == "boss"
    other = member_auth(client, "m2", "w1")
    r = client.patch(
        "/members/me",
        json={"handle": "boss"},
        headers=member_headers(client, "m2", "w1"),
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "handle_taken"
    r = client.patch(
        "/members/me",
        json={"handle": "Boss"},
        headers=member_headers(client, "m2", "w1"),
    )
    assert r.status_code == 422  # uppercase fails the pattern


def test_patch_explicit_null_handle_is_422(client):
    """Explicit JSON null must be rejected like the sibling required fields,

    not fall through to a DB NOT NULL violation surfaced as a generic 409.
    """
    response = client.patch(
        "/members/me",
        json={"handle": None},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 422
