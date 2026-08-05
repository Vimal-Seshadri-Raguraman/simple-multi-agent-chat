"""Name uniqueness: workspaces global, channels per workspace, case-insensitive."""

import sqlalchemy

import app.database as database_module
from app.models import Workspace
from tests.conftest import founder_auth, founder_headers, general_channel_id


def _found(client, name, email):
    account_token = client.post(
        "/accounts", json={"email": email, "password": "a-strong-password"}
    ).json()["tokens"]["access_token"]
    return client.post(
        "/workspaces",
        json={
            "workspace_name": name,
            "visibility": "private",
            "display_first_name": "Test",
            "display_last_name": "User",
        },
        headers={"Authorization": f"Bearer {account_token}"},
    )


def test_duplicate_workspace_name_409(client):
    assert _found(client, "AI Finance Co", "a@test.example").status_code == 200
    response = _found(client, "AI Finance Co", "b@test.example")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "workspace_name_taken"
    assert "AI Finance Co" in response.json()["error"]["message"]


def test_workspace_name_case_insensitive_and_leak_accepted(client):
    """Case variants collide -- including against PRIVATE workspaces (the
    accepted existence leak from the design's Decision 1)."""
    assert _found(client, "AI Finance Co", "a@test.example").status_code == 200
    assert _found(client, "ai finance CO", "b@test.example").status_code == 409


def test_workspace_casing_preserved(client):
    body = _found(client, "AI Finance Co", "a@test.example").json()
    assert body["workspace"]["workspace_name"] == "AI Finance Co"


def test_duplicate_channel_name_in_workspace_409(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    headers = founder_headers(client, "w1")
    assert (
        client.post(
            f"/workspaces/{ws}/channels",
            json={"channel_name": "reports"},
            headers=headers,
        ).status_code
        == 200
    )
    response = client.post(
        f"/workspaces/{ws}/channels", json={"channel_name": "Reports"}, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "channel_name_taken"


def test_duplicate_of_general_409(client):
    """The default channel already claims 'general'."""
    ws = founder_auth(client, "w1")["workspace_id"]
    response = client.post(
        f"/workspaces/{ws}/channels",
        json={"channel_name": "General"},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 409


def test_same_channel_name_across_workspaces_ok(client):
    """Every workspace has a general -- cross-workspace duplication is required."""
    founder_auth(client, "w1")
    founder_auth(client, "w2")
    assert general_channel_id(client, "w1") != general_channel_id(client, "w2")


def test_db_backstop_rejects_bypass(client):
    """A write that skips the pre-check hits the unique index (IntegrityError),
    which in a real request would surface via the global 409 conflict handler."""
    founder_auth(client, "w1")  # workspace named "w1-workspace"
    with database_module.SessionLocal() as db:
        db.add(Workspace(workspace_name="W1-WORKSPACE", visibility="private"))
        try:
            db.commit()
            raise AssertionError("expected IntegrityError from uq_workspaces_name_ci")
        except sqlalchemy.exc.IntegrityError:
            db.rollback()
