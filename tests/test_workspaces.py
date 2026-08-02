"""Tests for workspace founding (POST /workspaces) and workspace-scoped member listing."""

import app.database as database_module
from app.models import ChannelMember, Member, Workspace, WorkspaceRecord
from app.security import create_access_token
from tests.conftest import founder_auth, founder_headers, member_auth

FOUND_BODY = {
    "workspace_name": "Acme",
    "email": "founder@test.example",
    "password": "s3cret-password",
    "first_name": "Ada",
    "last_name": "Lovelace",
}


def test_founding_defaults_to_private_visibility(client):
    response = client.post("/workspaces", json=FOUND_BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["workspace"]["workspace_name"] == "Acme"
    assert body["workspace"]["visibility"] == "private"
    assert body["member"]["member_type"] == "human"
    assert body["access_token"] and body["refresh_token"]


def test_founding_public_workspace(client):
    response = client.post("/workspaces", json=dict(FOUND_BODY, visibility="public"))
    assert response.json()["workspace"]["visibility"] == "public"


def test_founder_is_admin(client):
    response = client.post("/workspaces", json=FOUND_BODY)
    member_id = response.json()["member"]["member_id"]
    with database_module.SessionLocal() as db:
        member = db.get(Member, member_id)
        assert member.is_admin is True


def test_founding_creates_workspace_record(client):
    response = client.post("/workspaces", json=FOUND_BODY)
    body = response.json()
    with database_module.SessionLocal() as db:
        record = db.get(WorkspaceRecord, body["workspace"]["workspace_id"])
        assert record is not None
        assert record.created_by == body["member"]["member_id"]
        assert record.status == "active"


def test_founding_creates_general_channel_with_founder_inside(client):
    response = client.post("/workspaces", json=FOUND_BODY)
    body = response.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    ws_id = body["workspace"]["workspace_id"]

    channels = client.get(f"/workspaces/{ws_id}/channels", headers=headers).json()
    assert [c["channel_name"] for c in channels] == ["general"]

    general_id = channels[0]["channel_id"]
    channel_members = client.get(
        f"/workspaces/{ws_id}/channels/{general_id}/members", headers=headers
    ).json()
    assert body["member"]["member_id"] in [m["member_id"] for m in channel_members]


def test_list_workspace_members_includes_founder_and_registered_member(client):
    founder = founder_auth(client, "w1")
    member = member_auth(client, "m2", "w1")
    response = client.get(
        f"/workspaces/{founder['workspace_id']}/members",
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200
    member_ids = [m["member_id"] for m in response.json()]
    assert founder["member_id"] in member_ids
    assert member["member_id"] in member_ids


def test_list_workspace_members_requires_auth(client):
    founder = founder_auth(client, "w1")
    response = client.get(f"/workspaces/{founder['workspace_id']}/members")
    assert response.status_code == 401


def test_list_workspace_members_wall_blocks_foreign_workspace(client):
    founder_auth(client, "w1")
    foreign = founder_auth(client, "w2")
    response = client.get(
        f"/workspaces/{foreign['workspace_id']}/members",
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_workspace_with_null_default_channel_supports_registration_and_listing(client):
    """A workspace whose default_channel_id is NULL (no 'general' channel) must
    still support registration and member listing -- there's just no channel
    to land new members in.

    Accounts always require an existing workspace, so the workspace (and a
    seed member to authenticate as, since there's no founding endpoint that
    skips channel creation) are inserted directly via the DB session.
    """
    with database_module.SessionLocal() as db:
        ws = Workspace(workspace_name="Legacy", visibility="public")
        db.add(ws)
        db.flush()
        seed = Member(
            workspace_id=ws.workspace_id,
            member_name="Seed",
            member_type="human",
            email="seed@test.example",
            is_admin=True,
        )
        db.add(seed)
        db.commit()
        ws_id, seed_id = ws.workspace_id, seed.member_id

    headers = {"Authorization": f"Bearer {create_access_token(seed_id)}"}

    response = client.post(
        f"/workspaces/{ws_id}/register",
        json={
            "email": "newcomer@test.example",
            "password": "s3cret-password",
            "first_name": "New",
            "last_name": "Comer",
        },
    )
    assert response.status_code == 200
    newcomer_id = response.json()["member"]["member_id"]

    members = client.get(f"/workspaces/{ws_id}/members", headers=headers).json()
    member_ids = [m["member_id"] for m in members]
    assert seed_id in member_ids
    assert newcomer_id in member_ids

    with database_module.SessionLocal() as db:
        assert db.query(ChannelMember).count() == 0
