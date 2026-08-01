"""Tests for auth resolution: Bearer JWT for humans, X-API-Key for agents/bots."""

import pytest

from app.auth import resolve_member
from app.errors import InvalidTokenError
from app.models import Member
from app.security import create_access_token
from tests.conftest import human_headers, human_member_id


def test_bearer_token_resolves_human(client):
    response = client.get(
        "/member",
        params={"id": human_member_id(client, "m_1")},
        headers=human_headers(client, "m_1"),
    )
    assert response.status_code == 200
    assert response.json()["member_type"] == "human"


def test_no_credentials_is_401(client):
    response = client.get("/workspaces")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_malformed_bearer_token_is_401_invalid_token(client):
    response = client.get(
        "/workspaces", headers={"Authorization": "Bearer garbage.token.here"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_token_for_deleted_member_rejected(db_session):
    token = create_access_token("no-such-member")
    with pytest.raises(InvalidTokenError):
        resolve_member(db_session, token, None)


def test_api_key_still_resolves_agent(client):
    agent = client.post(
        "/members/agents",
        json={"member_name": "Bot"},
        headers=human_headers(client, "m_1"),
    ).json()
    response = client.get("/workspaces", headers={"X-API-Key": agent["api_key"]})
    assert response.status_code == 200


def test_unknown_api_key_is_401(client):
    response = client.get("/workspaces", headers={"X-API-Key": "nope"})
    assert response.status_code == 401


def test_dev_headers_no_longer_work(client):
    """The old dev stub must be fully dead: headers are ignored → 401."""
    response = client.get(
        "/workspaces",
        headers={"X-Dev-Member-Id": "m_1", "X-Dev-Member-Name": "Alice"},
    )
    assert response.status_code == 401
    # And it must NOT have auto-created a member.
    member = client.get("/members", headers=human_headers(client, "checker")).json()
    assert all(m["member_name"] != "Alice" for m in member)
