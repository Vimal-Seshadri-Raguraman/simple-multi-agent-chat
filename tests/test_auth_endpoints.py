"""Endpoint tests for /auth/register and /auth/login."""

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


def _register(client) -> dict:
    return client.post("/auth/register", json=REGISTER_BODY).json()


def test_refresh_rotates_tokens(client):
    tokens = _register(client)
    response = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 200
    new_tokens = response.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # The old refresh token was rotated away and must now be rejected.
    replay = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "invalid_token"

    # The new one works.
    again = client.post(
        "/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert again.status_code == 200


def test_refresh_with_garbage_token_rejected(client):
    response = client.post("/auth/refresh", json={"refresh_token": "not-a-token"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_expired_refresh_token_rejected(client):
    """An expired row is rejected and deleted when presented."""
    from datetime import timedelta

    import app.database as database_module
    from app.models import RefreshToken
    from app.models import utcnow
    from app.security import hash_token

    tokens = _register(client)
    with database_module.SessionLocal() as db:
        row = db.get(RefreshToken, hash_token(tokens["refresh_token"]))
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.add(row)
        db.commit()

    response = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 401
    with database_module.SessionLocal() as db:
        assert db.get(RefreshToken, hash_token(tokens["refresh_token"])) is None


def test_logout_kills_refresh_token(client):
    tokens = _register(client)
    response = client.post(
        "/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "logged_out"}

    replay = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert replay.status_code == 401


def test_logout_requires_auth(client):
    tokens = _register(client)
    response = client.post(
        "/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 401


def test_logout_cannot_kill_another_members_token(client):
    tokens_a = _register(client)
    tokens_b = client.post(
        "/auth/register", json=dict(REGISTER_BODY, email="bob@example.com")
    ).json()
    # A tries to revoke B's refresh token: 200 (idempotent, no leak) but B's
    # token must still work afterwards.
    client.post(
        "/auth/logout",
        json={"refresh_token": tokens_b["refresh_token"]},
        headers={"Authorization": f"Bearer {tokens_a['access_token']}"},
    )
    response = client.post(
        "/auth/refresh", json={"refresh_token": tokens_b["refresh_token"]}
    )
    assert response.status_code == 200
