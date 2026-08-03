"""Two-tier auth (SMAC-79 Task 1, spec §2): account-scope vs
workspace-scope tokens are enforced in BOTH directions, refresh echoes
the stored scope, and every legacy (pre-Identity-v2) token keeps working
exactly as before -- the Task-1 invariant (nothing legacy changes).
"""

from tests.conftest import founder_auth, founder_headers

_PASSWORD = "test-password-123"  # matches conftest._TEST_PASSWORD


def _account_tokens_for_founder(client, key: str = "tier"):
    """Found a workspace via the legacy door, then log in at the account
    tier for that same (dual-written) email -- how a real client
    bootstraps an account token today, since Task 1 doesn't touch the
    birth doors themselves."""
    founder = founder_auth(client, key)
    response = client.post(
        "/accounts/login", json={"email": f"{key}@test.example", "password": _PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json(), founder


def test_account_token_rejected_by_workspace_endpoint(client):
    body, founder = _account_tokens_for_founder(client, "wall-a")
    response = client.get(
        f"/workspaces/{founder['workspace_id']}/members",
        headers={"Authorization": f"Bearer {body['tokens']['access_token']}"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "workspace_token_required"


def test_workspace_token_rejected_by_account_endpoint(client):
    """Reverse direction: a workspace-tier (here, legacy) token must not
    satisfy an account-scope endpoint."""
    response = client.get("/accounts/me", headers=founder_headers(client, "wall-b"))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "account_token_required"


def test_mint_workspace_token_from_account_token(client):
    body, founder = _account_tokens_for_founder(client, "mint-a")
    minted = client.post(
        f"/workspaces/{founder['workspace_id']}/token",
        headers={"Authorization": f"Bearer {body['tokens']['access_token']}"},
    )
    assert minted.status_code == 200
    tokens = minted.json()
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["token_type"] == "bearer"
    # The new workspace-tier token is accepted by a workspace endpoint.
    response = client.get(
        f"/workspaces/{founder['workspace_id']}/members",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 200


def test_mint_workspace_token_requires_account_token(client):
    """A legacy/workspace token presented to the minting endpoint itself
    is rejected -- wrong tier, not just "any token will do"."""
    founder = founder_auth(client, "mint-b")
    response = client.post(
        f"/workspaces/{founder['workspace_id']}/token",
        headers={"Authorization": f"Bearer {founder['access_token']}"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "account_token_required"


def test_mint_workspace_token_non_membership_is_uniform_404(client):
    """An account with no membership in a REAL, foreign workspace gets
    the same 404 *code* as a nonexistent workspace_id -- the wall.
    (Message text legitimately embeds the caller-supplied workspace_id,
    same as every other NotFoundError in this codebase, e.g.
    require_same_workspace -- so it differs by id, not by outcome.)"""
    body, _ = _account_tokens_for_founder(client, "mint-c")
    other_founder = founder_auth(client, "mint-c-other")
    real_but_foreign = client.post(
        f"/workspaces/{other_founder['workspace_id']}/token",
        headers={"Authorization": f"Bearer {body['tokens']['access_token']}"},
    )
    nonexistent = client.post(
        "/workspaces/does-not-exist/token",
        headers={"Authorization": f"Bearer {body['tokens']['access_token']}"},
    )
    assert real_but_foreign.status_code == nonexistent.status_code == 404
    assert (
        real_but_foreign.json()["error"]["code"]
        == nonexistent.json()["error"]["code"]
        == "not_found"
    )


def test_refresh_preserves_workspace_scope(client):
    body, founder = _account_tokens_for_founder(client, "refresh-a")
    minted = client.post(
        f"/workspaces/{founder['workspace_id']}/token",
        headers={"Authorization": f"Bearer {body['tokens']['access_token']}"},
    ).json()
    refreshed = client.post(
        "/auth/refresh", json={"refresh_token": minted["refresh_token"]}
    )
    assert refreshed.status_code == 200
    new_access = refreshed.json()["access_token"]
    response = client.get(
        f"/workspaces/{founder['workspace_id']}/members",
        headers={"Authorization": f"Bearer {new_access}"},
    )
    assert response.status_code == 200
    # Still workspace-tier, not account-tier, after rotation.
    denied = client.get(
        "/accounts/me", headers={"Authorization": f"Bearer {new_access}"}
    )
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "account_token_required"


def test_refresh_preserves_account_scope(client):
    body, _ = _account_tokens_for_founder(client, "refresh-b")
    refreshed = client.post(
        "/auth/refresh", json={"refresh_token": body["tokens"]["refresh_token"]}
    )
    assert refreshed.status_code == 200
    new_access = refreshed.json()["access_token"]
    response = client.get(
        "/accounts/me", headers={"Authorization": f"Bearer {new_access}"}
    )
    assert response.status_code == 200
    # Still account-tier, not workspace-tier, after rotation.
    denied = client.get("/members", headers={"Authorization": f"Bearer {new_access}"})
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "workspace_token_required"


def test_legacy_tokens_still_work_on_workspace_endpoints(client):
    """Task-1 invariant: a token minted by the untouched legacy path
    (POST /workspaces founding) keeps working on workspace endpoints."""
    founder = founder_auth(client, "legacy-tier")
    response = client.get(
        f"/workspaces/{founder['workspace_id']}/members",
        headers={"Authorization": f"Bearer {founder['access_token']}"},
    )
    assert response.status_code == 200


def test_legacy_refresh_still_works(client):
    """A refresh token minted by the legacy path (row.scope reads back as
    the column's server default "workspace") still rotates correctly."""
    response = client.post(
        "/workspaces",
        json={
            "workspace_name": "Legacy Refresh Co",
            "visibility": "private",
            "email": "legacyrefresh@test.example",
            "password": _PASSWORD,
            "first_name": "Leg",
            "last_name": "Acy",
        },
    )
    tokens = response.json()
    refreshed = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    check = client.get(
        f"/workspaces/{tokens['workspace']['workspace_id']}/members",
        headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"},
    )
    assert check.status_code == 200


def test_api_key_auth_unaffected_by_token_tiers(client):
    """X-API-Key auth (agents/bots) doesn't go through the JWT-scope
    machinery at all -- unchanged (spec §2)."""
    agent = client.post(
        "/members/agents",
        json={"member_name": "Tier Bot"},
        headers=founder_headers(client, "api-key-tier"),
    ).json()
    response = client.get("/members", headers={"X-API-Key": agent["api_key"]})
    assert response.status_code == 200
