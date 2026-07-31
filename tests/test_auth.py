import app.auth
from app.auth import generate_api_key, hash_api_key
from app.models import Member
from tests.conftest import human_headers


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
    register = client.post(
        "/members/agents",
        json={"member_name": "Research-Bot"},
        headers=human_headers("m_1"),
    )
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


def test_dev_headers_rejected_when_flag_is_off(monkeypatch, db_session):
    """Security: dev headers must be rejected when ALLOW_DEV_AUTH_HEADERS is false."""
    # Gate the dev header path by setting the flag to false
    monkeypatch.setattr(app.auth, "ALLOW_DEV_AUTH_HEADERS", False)

    # When flag is off, resolve_member should ignore dev headers and return None
    result = app.auth.resolve_member(
        db_session, dev_member_id="m_1", dev_member_name="Alice", api_key=None
    )
    assert result is None, "Dev headers should be ignored when flag is off"

    # When flag is on, it should auto-create the member
    monkeypatch.setattr(app.auth, "ALLOW_DEV_AUTH_HEADERS", True)
    result = app.auth.resolve_member(
        db_session, dev_member_id="m_2", dev_member_name="Bob", api_key=None
    )
    assert result is not None, "Dev headers should work when flag is on"
    assert result.member_id == "m_2"
    assert result.member_name == "Bob"
    assert result.member_type == "human"
