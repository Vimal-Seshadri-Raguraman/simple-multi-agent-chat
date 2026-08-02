"""End-to-end journeys through the Slack model."""

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest import (
    founder_auth,
    founder_headers,
    member_token,
)


def test_public_journey_search_register_post(client):
    founder_auth(client, "acme")  # public
    found = client.get("/workspaces/search", params={"name": "acme"}).json()
    ws = found[0]["workspace_id"]
    joined = client.post(
        f"/workspaces/{ws}/register",
        json={
            "email": "newbie@test.example",
            "password": "newbie-pass-1",
            "first_name": "New",
            "last_name": "Bie",
        },
    )
    assert joined.status_code == 200
    body = joined.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    channels = client.get(f"/workspaces/{ws}/channels", headers=headers).json()
    general = [c for c in channels if c["channel_name"] == "general"][0]
    posted = client.post(
        f"/workspaces/{ws}/channels/{general['channel_id']}/messages",
        json={"message_text": "hi!"},
        headers=headers,
    )
    assert posted.status_code == 200
    assert posted.json()["Sender"]["member_id"] == body["member"]["member_id"]


def test_private_journey_reserved_seat(client):
    ws = founder_auth(client, "sec", visibility="private")["workspace_id"]
    client.post(
        f"/workspaces/{ws}/invites",
        json={"invite_type": "email", "email": "vip@test.example"},
        headers=founder_headers(client, "sec", visibility="private"),
    )
    # Uninvited email: uniform 404 (workspace never confirms existence)
    r = client.post(
        f"/workspaces/{ws}/register",
        json={
            "email": "rando@test.example",
            "password": "rando-pass-1",
            "first_name": "Ran",
            "last_name": "Do",
        },
    )
    assert r.status_code == 404
    # Invited email: in, and the seat is consumed
    r = client.post(
        f"/workspaces/{ws}/register",
        json={
            "email": "VIP@test.example",
            "password": "vip-pass-12",
            "first_name": "Vi",
            "last_name": "P",
        },
    )
    assert r.status_code == 200
    invites = client.get(
        f"/workspaces/{ws}/invites",
        headers=founder_headers(client, "sec", visibility="private"),
    ).json()
    assert invites == []


def test_same_email_two_workspaces_distinct_accounts(client):
    a = founder_auth(client, "wa")["workspace_id"]
    b = founder_auth(client, "wb")["workspace_id"]
    acc = {
        "email": "dual@test.example",
        "password": "dual-pass-12",
        "first_name": "Du",
        "last_name": "Al",
    }
    ra = client.post(f"/workspaces/{a}/register", json=acc)
    rb = client.post(f"/workspaces/{b}/register", json=acc)
    assert ra.status_code == rb.status_code == 200
    assert ra.json()["member"]["member_id"] != rb.json()["member"]["member_id"]
    # login is workspace-scoped
    la = client.post(
        "/auth/login",
        json={"workspace_id": a, **{k: acc[k] for k in ("email", "password")}},
    )
    assert la.status_code == 200


def test_the_wall(client):
    a = founder_auth(client, "wa")["workspace_id"]
    founder_auth(client, "wb")
    intruder = founder_headers(client, "wb")
    channels = client.get(f"/workspaces/{a}/channels", headers=intruder)
    members = client.get(f"/workspaces/{a}/members", headers=intruder)
    invites = client.get(f"/workspaces/{a}/invites", headers=intruder)
    assert channels.status_code == members.status_code == invites.status_code == 404


def test_login_failures_byte_identical(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    wrong_pw = client.post(
        "/auth/login",
        json={
            "workspace_id": ws,
            "email": "w1@test.example",
            "password": "wrong-pass-1",
        },
    )
    wrong_ws = client.post(
        "/auth/login",
        json={
            "workspace_id": "nope",
            "email": "w1@test.example",
            "password": "wrong-pass-1",
        },
    )
    unknown = client.post(
        "/auth/login",
        json={
            "workspace_id": ws,
            "email": "ghost@test.example",
            "password": "wrong-pass-1",
        },
    )
    assert wrong_pw.status_code == wrong_ws.status_code == unknown.status_code == 401
    assert wrong_pw.json() == wrong_ws.json() == unknown.json()


def test_retired_endpoints_are_gone(client):
    assert client.post("/auth/register", json={}).status_code in (404, 405)
    headers = founder_headers(client, "w1")
    assert client.get("/invites", headers=headers).status_code in (404, 405)
    assert client.post("/invites/x/accept", headers=headers).status_code in (404, 405)
    assert client.post("/invites/x/decline", headers=headers).status_code in (404, 405)
    assert client.get("/workspaces", headers=headers).status_code in (404, 405)
    ws = founder_auth(client, "w1")["workspace_id"]
    assert client.post(
        f"/workspaces/{ws}/members", json={"member_id": "x"}, headers=headers
    ).status_code in (404, 405)


def test_websocket_wall_cross_workspace_rejection(client):
    """A member of workspace B cannot connect to workspace A's channel via WebSocket.

    The wall must extend to WebSocket connections: cross-workspace access attempts
    are rejected with close code 4404.
    """
    # Set up workspace A with a channel
    ws_a = founder_auth(client, "wa")["workspace_id"]
    channels_a = client.get(
        f"/workspaces/{ws_a}/channels", headers=founder_headers(client, "wa")
    ).json()
    general_a = [c for c in channels_a if c["channel_name"] == "general"][0]

    # Set up workspace B and get a member's token
    ws_b = founder_auth(client, "wb")["workspace_id"]
    member_b_token = member_token(client, "intruder", workspace_key="wb")

    # Attempt to connect to workspace A's channel using workspace B member's token
    ws_url = f"/ws/workspaces/{ws_a}/channels/{general_a['channel_id']}"

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"{ws_url}?token={member_b_token}"):
            pass
    assert exc_info.value.code == 4404
