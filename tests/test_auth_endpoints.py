"""Endpoint tests for workspace founding/registration and /auth/login."""

FOUND_BODY = {
    "workspace_name": "Wonderland",
    "email": "Alice@Example.com",
    "password": "s3cret-password",
    "first_name": "Alice",
    "last_name": "Liddell",
}


def test_founding_returns_profile_and_tokens(client):
    response = client.post("/workspaces", json=FOUND_BODY)
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
    assert body["workspace"]["workspace_name"] == "Wonderland"
    assert body["workspace"]["visibility"] == "private"


def test_founding_with_explicit_display_name(client):
    body = dict(FOUND_BODY, display_name="Wonder Alice", company="Wonderland Inc")
    member = client.post("/workspaces", json=body).json()["member"]
    assert member["member_name"] == "Wonder Alice"
    assert member["company"] == "Wonderland Inc"


def test_founding_token_works_immediately(client):
    tokens = client.post("/workspaces", json=FOUND_BODY).json()
    ws_id = tokens["workspace"]["workspace_id"]
    response = client.get(
        f"/workspaces/{ws_id}/members",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200


def test_duplicate_email_in_same_workspace_conflicts(client):
    founded = client.post(
        "/workspaces", json=dict(FOUND_BODY, visibility="public")
    ).json()
    ws_id = founded["workspace"]["workspace_id"]
    response = client.post(
        f"/workspaces/{ws_id}/register",
        json={
            "email": "ALICE@example.com",  # case-insensitive dup of the founder
            "password": "another-password",
            "first_name": "Alice",
            "last_name": "Two",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


def test_short_password_rejected(client):
    response = client.post("/workspaces", json=dict(FOUND_BODY, password="short"))
    assert response.status_code == 422


def test_invalid_email_rejected(client):
    response = client.post("/workspaces", json=dict(FOUND_BODY, email="not-an-email"))
    assert response.status_code == 422


def _found(client) -> dict:
    return client.post("/workspaces", json=FOUND_BODY).json()


def test_login_returns_tokens(client):
    founded = _found(client)
    response = client.post(
        "/auth/login",
        json={
            "workspace_id": founded["workspace"]["workspace_id"],
            "email": "alice@example.com",
            "password": "s3cret-password",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["token_type"] == "bearer"


def test_login_wrong_password_unknown_email_and_wrong_workspace_identical(client):
    """No account-existence leak: all three failure modes return byte-identical bodies."""
    founded = _found(client)
    ws_id = founded["workspace"]["workspace_id"]
    wrong_password = client.post(
        "/auth/login",
        json={
            "workspace_id": ws_id,
            "email": "alice@example.com",
            "password": "wrong-pass",
        },
    )
    unknown_email = client.post(
        "/auth/login",
        json={
            "workspace_id": ws_id,
            "email": "nobody@example.com",
            "password": "wrong-pass",
        },
    )
    wrong_workspace = client.post(
        "/auth/login",
        json={
            "workspace_id": "does-not-exist",
            "email": "alice@example.com",
            "password": "s3cret-password",
        },
    )
    assert (
        wrong_password.status_code
        == unknown_email.status_code
        == wrong_workspace.status_code
        == 401
    )
    assert wrong_password.json() == unknown_email.json() == wrong_workspace.json()
    assert wrong_password.json()["error"]["code"] == "invalid_credentials"


def test_refresh_rotates_tokens(client):
    tokens = _found(client)
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

    tokens = _found(client)
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
    tokens = _found(client)
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
    tokens = _found(client)
    response = client.post(
        "/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 401


def test_logout_cannot_kill_another_members_token(client):
    tokens_a = _found(client)
    tokens_b = client.post(
        "/workspaces", json=dict(FOUND_BODY, email="bob@example.com")
    ).json()
    # A tries to revoke B's refresh token: 200 (idempotent, no leak) but B's
    # token must still work afterwards.
    logout_response = client.post(
        "/auth/logout",
        json={"refresh_token": tokens_b["refresh_token"]},
        headers={"Authorization": f"Bearer {tokens_a['access_token']}"},
    )
    assert logout_response.status_code == 200
    response = client.post(
        "/auth/refresh", json={"refresh_token": tokens_b["refresh_token"]}
    )
    assert response.status_code == 200
