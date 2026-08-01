"""Endpoint tests for /auth/register and /auth/login."""

import pytest

REGISTER_BODY = {
    "email": "Alice@Example.com",
    "password": "s3cret-password",
    "first_name": "Alice",
    "last_name": "Liddell",
}


def test_register_returns_profile_and_tokens(client):
    response = client.post("/auth/register", json=REGISTER_BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["access_token"] and body["refresh_token"]
    member = body["member"]
    assert member["member_type"] == "human"
    assert member["email"] == "alice@example.com"  # stored lowercased
    assert member["member_name"] == "Alice Liddell"  # display name defaulted
    assert member["first_name"] == "Alice"
    assert member["company"] is None


def test_register_with_explicit_display_name(client):
    body = dict(REGISTER_BODY, display_name="Wonder Alice", company="Wonderland Inc")
    member = client.post("/auth/register", json=body).json()["member"]
    assert member["member_name"] == "Wonder Alice"
    assert member["company"] == "Wonderland Inc"


@pytest.mark.xfail(
    reason="Bearer resolution lands in the auth cutover task", strict=True
)
def test_register_token_works_immediately(client):
    tokens = client.post("/auth/register", json=REGISTER_BODY).json()
    response = client.get(
        "/workspaces", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert response.status_code == 200


def test_duplicate_email_conflicts(client):
    client.post("/auth/register", json=REGISTER_BODY)
    second = dict(REGISTER_BODY, email="ALICE@example.com")  # case-insensitive dup
    response = client.post("/auth/register", json=second)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


def test_short_password_rejected(client):
    response = client.post("/auth/register", json=dict(REGISTER_BODY, password="short"))
    assert response.status_code == 422


def test_invalid_email_rejected(client):
    response = client.post(
        "/auth/register", json=dict(REGISTER_BODY, email="not-an-email")
    )
    assert response.status_code == 422


def test_login_returns_tokens(client):
    client.post("/auth/register", json=REGISTER_BODY)
    response = client.post(
        "/auth/login",
        json={"email": "alice@example.com", "password": "s3cret-password"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["token_type"] == "bearer"


def test_login_wrong_password_and_unknown_email_identical(client):
    """No account-existence leak: both failures return byte-identical bodies."""
    client.post("/auth/register", json=REGISTER_BODY)
    wrong_password = client.post(
        "/auth/login", json={"email": "alice@example.com", "password": "wrong-pass"}
    )
    unknown_email = client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "wrong-pass"}
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json()["error"]["code"] == "invalid_credentials"
