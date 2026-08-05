"""One end-to-end journey through the entire two-tier auth lifecycle
(Identity v2, SMAC-79 Task 2 cutover): signup -> found (account-authed,
convenience workspace tokens) -> refresh -> logout -> expired/invalid
tokens rejected."""

from datetime import datetime, timedelta, timezone

import jwt

from app.security import SECRET_KEY


def test_full_auth_lifecycle(client):
    # Global signup -> ACCOUNT tokens, auto-logged-in.
    signup = client.post(
        "/accounts",
        json={"email": "vimal@example.com", "password": "super-secret-1"},
    ).json()
    account_token = signup["tokens"]["access_token"]

    # Found a workspace (account-authed) -> logged in immediately with a
    # convenience WORKSPACE token pair.
    founded = client.post(
        "/workspaces",
        json={
            "workspace_name": "Home",
            "visibility": "private",
            "display_first_name": "Vimal",
            "display_last_name": "Raguraman",
        },
        headers={"Authorization": f"Bearer {account_token}"},
    ).json()
    member_id = founded["member"]["member_id"]
    workspace_id = founded["workspace"]["workspace_id"]
    headers = {"Authorization": f"Bearer {founded['access_token']}"}

    # Authenticated call works inside the founder's own workspace.
    assert (
        client.get(f"/workspaces/{workspace_id}/members", headers=headers).status_code
        == 200
    )

    # The account token cannot be used on a workspace endpoint (tier wall).
    denied = client.get(
        f"/workspaces/{workspace_id}/members",
        headers={"Authorization": f"Bearer {account_token}"},
    )
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "workspace_token_required"

    # A fresh workspace-token mint via the account token also works.
    minted = client.post(
        f"/workspaces/{workspace_id}/token",
        headers={"Authorization": f"Bearer {account_token}"},
    ).json()
    assert minted["access_token"]

    # Refresh rotates: old dies, new lives.
    refreshed = client.post(
        "/auth/refresh", json={"refresh_token": founded["refresh_token"]}
    ).json()
    assert (
        client.post(
            "/auth/refresh", json={"refresh_token": founded["refresh_token"]}
        ).status_code
        == 401
    )

    # New access token authenticates.
    new_headers = {"Authorization": f"Bearer {refreshed['access_token']}"}
    assert (
        client.get(
            f"/workspaces/{workspace_id}/members", headers=new_headers
        ).status_code
        == 200
    )

    # Logout kills the refresh token.
    client.post(
        "/auth/logout",
        json={"refresh_token": refreshed["refresh_token"]},
        headers=new_headers,
    )
    assert (
        client.post(
            "/auth/refresh", json={"refresh_token": refreshed["refresh_token"]}
        ).status_code
        == 401
    )

    # An expired access token (forged with the real key, workspace scope)
    # is rejected.
    expired = jwt.encode(
        {
            "sub": member_id,
            "scope": "workspace",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        SECRET_KEY,
        algorithm="HS256",
    )
    response = client.get(
        f"/workspaces/{workspace_id}/members",
        headers={"Authorization": f"Bearer {expired}"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
