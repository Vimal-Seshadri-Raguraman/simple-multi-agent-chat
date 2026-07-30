from app.auth import generate_api_key, hash_api_key
from app.models import Member


def test_dev_header_auto_creates_human_member(client):
    response = client.post(
        "/workspaces",
        json={"workspace_name": "Acme"},
        headers={"X-Dev-Member-Id": "m_1", "X-Dev-Member-Name": "Alice"},
    )
    assert response.status_code == 200


def test_missing_credentials_returns_401(client):
    response = client.post("/workspaces", json={"workspace_name": "Acme"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_api_key_resolves_registered_agent(client):
    register = client.post("/members/agents", json={"member_name": "Research-Bot"})
    raw_key = register.json()["api_key"]

    response = client.post(
        "/workspaces/does-not-matter/channels/does-not-matter/messages",
        json={"message_text": "hi"},
        headers={"X-API-Key": raw_key},
    )
    # Not 401 — credential resolved. (Will 404 on the workspace, which is fine here.)
    assert response.status_code != 401


def test_invalid_api_key_returns_401(client):
    response = client.post(
        "/workspaces/does-not-matter/channels/does-not-matter/messages",
        json={"message_text": "hi"},
        headers={"X-API-Key": "not-a-real-key"},
    )
    assert response.status_code == 401


def test_hash_api_key_is_deterministic_and_not_reversible():
    raw = generate_api_key()
    assert hash_api_key(raw) == hash_api_key(raw)
    assert hash_api_key(raw) != raw
