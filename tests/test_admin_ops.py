"""Admin operations: visibility changes and admin promotion/demotion."""

from tests.conftest import founder_auth, founder_headers, member_auth, member_headers


def _ws(client):
    return founder_auth(client, "w1")["workspace_id"]


def test_founder_is_admin_and_can_flip_visibility(client):
    ws = _ws(client)
    r = client.patch(
        f"/workspaces/{ws}",
        json={"visibility": "private"},
        headers=founder_headers(client, "w1"),
    )
    assert r.status_code == 200 and r.json()["visibility"] == "private"
    r = client.patch(
        f"/workspaces/{ws}",
        json={"visibility": "public"},
        headers=founder_headers(client, "w1"),
    )
    assert r.json()["visibility"] == "public"


def test_non_admin_member_gets_403(client):
    ws = _ws(client)
    member_auth(client, "m2", "w1")
    r = client.patch(
        f"/workspaces/{ws}",
        json={"visibility": "private"},
        headers=member_headers(client, "m2", "w1"),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "not_workspace_admin"


def test_outsider_gets_uniform_404(client):
    ws = _ws(client)
    founder_auth(client, "w2")
    r = client.patch(
        f"/workspaces/{ws}",
        json={"visibility": "private"},
        headers=founder_headers(client, "w2"),
    )
    assert r.status_code == 404


def test_promote_then_demote(client):
    ws = _ws(client)
    m2 = member_auth(client, "m2", "w1")
    url = f"/workspaces/{ws}/members/{m2['member_id']}"
    r = client.patch(
        url, json={"is_admin": True}, headers=founder_headers(client, "w1")
    )
    assert r.status_code == 200
    # The new admin can act as one:
    r = client.patch(
        f"/workspaces/{ws}",
        json={"visibility": "public"},
        headers=member_headers(client, "m2", "w1"),
    )
    assert r.status_code == 200
    r = client.patch(
        url, json={"is_admin": False}, headers=founder_headers(client, "w1")
    )
    assert r.status_code == 200


def test_last_admin_cannot_demote_self(client):
    ws = _ws(client)
    founder_id = founder_auth(client, "w1")["member_id"]
    r = client.patch(
        f"/workspaces/{ws}/members/{founder_id}",
        json={"is_admin": False},
        headers=founder_headers(client, "w1"),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "last_admin"


def test_agent_cannot_be_promoted(client):
    ws = _ws(client)
    agent = client.post(
        "/members/agents",
        json={"member_name": "Bot"},
        headers=founder_headers(client, "w1"),
    ).json()
    r = client.patch(
        f"/workspaces/{ws}/members/{agent['member_id']}",
        json={"is_admin": True},
        headers=founder_headers(client, "w1"),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden_member_type"
