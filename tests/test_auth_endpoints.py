"""Endpoint tests for workspace founding/registration and the two-tier
token lifecycle (Identity v2, SMAC-79 Task 2 cutover: `/auth/login` and
`/auth/discover` are retired -- `POST /accounts/login` is their permanent
successor, tested in `test_accounts_v2.py`; this file covers founding,
registration, refresh, and logout under the new account-authed contract).
"""

from tests.conftest import founder_auth

_PASSWORD = "s3cret-password"


def _account(client, email: str) -> dict:
    response = client.post("/accounts", json={"email": email, "password": _PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


def _account_headers(client, email: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_account(client, email)['tokens']['access_token']}"
    }


FOUND_BODY = {
    "workspace_name": "Wonderland",
    "visibility": "private",
    "display_first_name": "Alice",
    "display_last_name": "Liddell",
}


def test_founding_returns_profile_and_tokens(client):
    response = client.post(
        "/workspaces",
        json=FOUND_BODY,
        headers=_account_headers(client, "alice@example.com"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["access_token"] and body["refresh_token"]
    member = body["member"]
    assert member["member_type"] == "human"
    assert member["member_name"] == "Alice Liddell"  # display name defaulted
    assert member["first_name"] == "Alice"
    assert member["company"] is None
    assert body["workspace"]["workspace_name"] == "Wonderland"
    assert body["workspace"]["visibility"] == "private"


def test_founding_with_explicit_display_name(client):
    body = dict(FOUND_BODY, workspace_name="Wonderland Two")
    account = _account(client, "alice-dn@example.com")
    account_token = account["tokens"]["access_token"]
    founded = client.post(
        "/workspaces",
        json=body,
        headers={"Authorization": f"Bearer {account_token}"},
    ).json()
    # display_first_name/display_last_name feed the DEFAULT display name;
    # PATCH /members/me is the only way to set an explicit one afterward.
    patched = client.patch(
        "/members/me",
        json={"display_name": "Wonder Alice", "company": "Wonderland Inc"},
        headers={"Authorization": f"Bearer {founded['access_token']}"},
    )
    assert patched.status_code == 200
    assert patched.json()["member_name"] == "Wonder Alice"
    assert patched.json()["company"] == "Wonderland Inc"


def test_founding_token_works_immediately(client):
    tokens = client.post(
        "/workspaces",
        json=FOUND_BODY,
        headers=_account_headers(client, "alice-imm@example.com"),
    ).json()
    ws_id = tokens["workspace"]["workspace_id"]
    response = client.get(
        f"/workspaces/{ws_id}/members",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200


def test_founding_requires_account_token(client):
    response = client.post("/workspaces", json=FOUND_BODY)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_workspace_token_cannot_found_a_workspace(client):
    """A workspace-tier token (the wrong tier) is rejected the same way
    an account-scope endpoint always rejects workspace tokens."""
    founder = founder_auth(client, "found-wrong-tier")
    response = client.post(
        "/workspaces",
        json=dict(FOUND_BODY, workspace_name="Wrong Tier Co"),
        headers={"Authorization": f"Bearer {founder['access_token']}"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "account_token_required"


def test_duplicate_registration_same_account_same_workspace_conflicts(client):
    """The old "duplicate email in same workspace" leak is now "the same
    ACCOUNT trying to register into a workspace it's already in" --
    `uq_members_workspace_account`'s invariant, via AlreadyAMemberError."""
    account_headers = _account_headers(client, "alice-dup@example.com")
    founded = client.post(
        "/workspaces",
        json=dict(FOUND_BODY, workspace_name="Wonderland Dup", visibility="public"),
        headers=account_headers,
    ).json()
    ws_id = founded["workspace"]["workspace_id"]
    response = client.post(
        f"/workspaces/{ws_id}/register",
        json={"first_name": "Alice", "last_name": "Two"},
        headers=account_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_a_member"


def test_short_password_rejected(client):
    response = client.post(
        "/accounts", json={"email": "short-pw@example.com", "password": "short"}
    )
    assert response.status_code == 422


def test_invalid_email_rejected(client):
    response = client.post(
        "/accounts", json={"email": "not-an-email", "password": _PASSWORD}
    )
    assert response.status_code == 422


def test_refresh_rotates_tokens(client):
    tokens = client.post(
        "/workspaces",
        json=dict(FOUND_BODY, workspace_name="Wonderland Refresh"),
        headers=_account_headers(client, "alice-refresh@example.com"),
    ).json()
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

    tokens = client.post(
        "/workspaces",
        json=dict(FOUND_BODY, workspace_name="Wonderland Expired"),
        headers=_account_headers(client, "alice-expired@example.com"),
    ).json()
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
    tokens = client.post(
        "/workspaces",
        json=dict(FOUND_BODY, workspace_name="Wonderland Logout"),
        headers=_account_headers(client, "alice-logout@example.com"),
    ).json()
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
    tokens = client.post(
        "/workspaces",
        json=dict(FOUND_BODY, workspace_name="Wonderland Logout Auth"),
        headers=_account_headers(client, "alice-logout-auth@example.com"),
    ).json()
    response = client.post(
        "/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 401


def test_logout_cannot_kill_another_members_token(client):
    tokens_a = client.post(
        "/workspaces",
        json=dict(FOUND_BODY, workspace_name="Underland A"),
        headers=_account_headers(client, "alice-a@example.com"),
    ).json()
    tokens_b = client.post(
        "/workspaces",
        json=dict(FOUND_BODY, workspace_name="Underland B"),
        headers=_account_headers(client, "bob-b@example.com"),
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


def test_logout_revokes_account_tier_refresh_token(client):
    """Final-review IMPORTANT-2: `/auth/logout` used to require a
    workspace token (`get_current_member`), so an account that has never
    entered a workspace had no way to log out at all. An account-tier
    caller presenting its OWN account-tier refresh token must now
    actually revoke it -- proven by the subsequent refresh failing, not
    just a 200 status (a 200-but-nothing-deleted response is exactly the
    bug this replaces)."""
    account = client.post(
        "/accounts",
        json={"email": "bare-account@example.com", "password": "s3cret-password"},
    ).json()
    tokens = account["tokens"]
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


def test_logout_workspace_tier_still_works_unchanged(client):
    """Workspace-tier logout (the pre-existing, already-tested contract)
    must be unaffected by widening `/auth/logout` to accept account
    tokens too."""
    tokens = client.post(
        "/workspaces",
        json=dict(FOUND_BODY, workspace_name="Wonderland Logout Unchanged"),
        headers=_account_headers(client, "alice-logout-unchanged@example.com"),
    ).json()
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


def test_logout_cross_scope_presentation_cannot_delete_wrong_row(client):
    """Final-review IMPORTANT-2, second half: holding a WORKSPACE token
    and presenting the caller's own ACCOUNT-tier refresh token (or vice
    versa) must not delete it -- the row match is scope-correct, not just
    "any refresh token the request can name". Idempotent 200 either way
    (anti-probing, unchanged), but the presented token must still be
    live afterwards.
    """
    account = client.post(
        "/accounts",
        json={
            "email": "alice-cross-scope@example.com",
            "password": "s3cret-password",
        },
    ).json()
    account_tokens = account["tokens"]
    workspace_tokens = client.post(
        "/workspaces",
        json=dict(FOUND_BODY, workspace_name="Wonderland Cross Scope"),
        headers={"Authorization": f"Bearer {account_tokens['access_token']}"},
    ).json()

    # Workspace-tier access token presenting the ACCOUNT-tier refresh
    # token: 200 (idempotent), but nothing is actually revoked.
    cross_response = client.post(
        "/auth/logout",
        json={"refresh_token": account_tokens["refresh_token"]},
        headers={"Authorization": f"Bearer {workspace_tokens['access_token']}"},
    )
    assert cross_response.status_code == 200
    still_live = client.post(
        "/auth/refresh", json={"refresh_token": account_tokens["refresh_token"]}
    )
    assert still_live.status_code == 200

    # Account-tier access token presenting the WORKSPACE-tier refresh
    # token: same story, reversed.
    cross_response_2 = client.post(
        "/auth/logout",
        json={"refresh_token": workspace_tokens["refresh_token"]},
        headers={"Authorization": f"Bearer {account_tokens['access_token']}"},
    )
    assert cross_response_2.status_code == 200
    workspace_refresh_still_live = client.post(
        "/auth/refresh", json={"refresh_token": workspace_tokens["refresh_token"]}
    )
    assert workspace_refresh_still_live.status_code == 200


def test_password_byte_cap_not_char_cap(client):
    """bcrypt's 72-byte limit is enforced at signup (POST /accounts) now
    -- the workspace join doors no longer carry a password at all."""
    # 40 two-byte chars = 80 bytes but only 40 characters: must be rejected
    r = client.post(
        "/accounts", json={"email": "multi@test.example", "password": "é" * 40}
    )
    assert r.status_code == 422
    # Exactly 72 bytes (36 two-byte chars): allowed
    r = client.post(
        "/accounts", json={"email": "edge@test.example", "password": "é" * 36}
    )
    assert r.status_code == 200
    # 72 ASCII chars = 72 bytes: allowed
    r = client.post(
        "/accounts", json={"email": "ascii@test.example", "password": "x" * 72}
    )
    assert r.status_code == 200
    # 73 ASCII chars: rejected
    r = client.post(
        "/accounts", json={"email": "long@test.example", "password": "x" * 73}
    )
    assert r.status_code == 422
