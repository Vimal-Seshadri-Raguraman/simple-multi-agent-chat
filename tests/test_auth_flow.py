"""One end-to-end journey through the entire auth lifecycle."""

from datetime import datetime, timedelta, timezone

import jwt

from app.security import SECRET_KEY


def test_full_auth_lifecycle(client):
    # Found a workspace → logged in immediately.
    founded = client.post(
        "/workspaces",
        json={
            "workspace_name": "Home",
            "email": "vimal@example.com",
            "password": "super-secret-1",
            "first_name": "Vimal",
            "last_name": "Raguraman",
            "company": "RIT",
        },
    ).json()
    member_id = founded["member"]["member_id"]
    workspace_id = founded["workspace"]["workspace_id"]
    headers = {"Authorization": f"Bearer {founded['access_token']}"}

    # Authenticated call works inside the founder's own workspace.
    assert (
        client.get(f"/workspaces/{workspace_id}/members", headers=headers).status_code
        == 200
    )

    # Fresh login also works.
    logged_in = client.post(
        "/auth/login",
        json={
            "workspace_id": workspace_id,
            "email": "vimal@example.com",
            "password": "super-secret-1",
        },
    ).json()
    assert logged_in["access_token"]

    # Refresh rotates: old dies, new lives.
    refreshed = client.post(
        "/auth/refresh", json={"refresh_token": logged_in["refresh_token"]}
    ).json()
    assert (
        client.post(
            "/auth/refresh", json={"refresh_token": logged_in["refresh_token"]}
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

    # An expired access token (forged with the real key) is rejected.
    expired = jwt.encode(
        {"sub": member_id, "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        SECRET_KEY,
        algorithm="HS256",
    )
    response = client.get(
        f"/workspaces/{workspace_id}/members",
        headers={"Authorization": f"Bearer {expired}"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
