"""SMAC-92: DELETE /workspaces/{id}/members/{id} -- member removal.

Guards (self-removal, last-admin), the dead-membership-token behavior
after removal, and the "history survives" invariant: a removed member's
past messages/mentions remain, rendered with a placeholder sender rather
than 500ing (Message.sender_member_id / Mention.mentioned_member_id are
nullable as of migration 7a3b580f5d0c, precisely so this works)."""

import app.database as database_module
from app.models import ChannelMember, Member
from tests.conftest import founder_auth, founder_headers, member_auth, member_headers


def _ws_and_target(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    target = member_auth(client, "target", "w1")
    return ws, target


def test_non_privileged_actor_gets_403(client):
    ws, target = _ws_and_target(client)
    r = client.delete(
        f"/workspaces/{ws}/members/{target['member_id']}",
        headers=member_headers(client, "target", "w1"),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_self_removal_rejected(client):
    founder = founder_auth(client, "w1")
    r = client.delete(
        f"/workspaces/{founder['workspace_id']}/members/{founder['member_id']}",
        headers=founder_headers(client, "w1"),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "self_removal"


def test_second_admin_can_remove_the_other_admin(client):
    """With two admins, removing one leaves an admin behind -- no
    last_admin block. (The reverse -- removing a workspace's ONLY admin --
    requires the caller to hold Cap.REMOVE_MEMBERS, which only the `admin`
    role grants; a solo admin removing "the only admin" is necessarily
    removing themselves, caught by the self-removal guard first. The
    last-admin check on this route is therefore defense-in-depth, mirroring
    update_member_role's identical invariant, for if a future role ever
    gains REMOVE_MEMBERS without being `admin`.)"""
    ws = founder_auth(client, "w1")["workspace_id"]
    m2 = member_auth(client, "m2", "w1")
    promote = client.patch(
        f"/workspaces/{ws}/members/{m2['member_id']}",
        json={"role": "admin"},
        headers=founder_headers(client, "w1"),
    )
    assert promote.status_code == 200

    founder_id = founder_auth(client, "w1")["member_id"]
    r = client.delete(
        f"/workspaces/{ws}/members/{founder_id}",
        headers=member_headers(client, "m2", "w1"),
    )
    assert r.status_code == 200

    with database_module.SessionLocal() as db:
        assert db.get(Member, m2["member_id"]).role == "admin"
        assert db.get(Member, founder_id) is None


def test_foreign_workspace_target_is_uniform_404(client):
    founder_auth(client, "w1")
    foreign = founder_auth(client, "w2")
    r = client.delete(
        f"/workspaces/{foreign['workspace_id']}/members/{foreign['member_id']}",
        headers=founder_headers(client, "w1"),
    )
    assert r.status_code == 404


def test_unknown_target_is_404(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    r = client.delete(
        f"/workspaces/{ws}/members/does-not-exist",
        headers=founder_headers(client, "w1"),
    )
    assert r.status_code == 404


def test_removal_deletes_channel_memberships_and_row(client):
    ws, target = _ws_and_target(client)
    with database_module.SessionLocal() as db:
        before = (
            db.query(ChannelMember)
            .filter(ChannelMember.member_id == target["member_id"])
            .count()
        )
        assert before >= 1  # auto-joined to the default channel on registration

    r = client.delete(
        f"/workspaces/{ws}/members/{target['member_id']}",
        headers=founder_headers(client, "w1"),
    )
    assert r.status_code == 200
    assert r.json() == {"status": "removed"}

    with database_module.SessionLocal() as db:
        assert db.get(Member, target["member_id"]) is None
        assert (
            db.query(ChannelMember)
            .filter(ChannelMember.member_id == target["member_id"])
            .count()
            == 0
        )


def test_removed_members_old_token_is_401_on_next_request(client):
    ws, target = _ws_and_target(client)
    stale_headers = member_headers(client, "target", "w1")

    r = client.delete(
        f"/workspaces/{ws}/members/{target['member_id']}",
        headers=founder_headers(client, "w1"),
    )
    assert r.status_code == 200

    r = client.get("/members/me", headers=stale_headers)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_token"


def test_history_survives_removal_with_placeholder_sender(client):
    """The removed member's own posted message must still be listable
    afterward -- not 500 -- rendered with a "(removed member)" placeholder
    Sender (SMAC-92: sender_member_id goes NULL, not the message itself)."""
    ws, target = _ws_and_target(client)
    target_headers = member_headers(client, "target", "w1")

    channels = client.get(f"/workspaces/{ws}/channels", headers=target_headers).json()
    general = [c for c in channels if c["channel_name"] == "general"][0]["channel_id"]

    posted = client.post(
        f"/workspaces/{ws}/channels/{general}/messages",
        json={"message_text": "hello from a member about to be removed"},
        headers=target_headers,
    )
    assert posted.status_code == 200
    message_id = posted.json()["Message"]["message_id"]

    r = client.delete(
        f"/workspaces/{ws}/members/{target['member_id']}",
        headers=founder_headers(client, "w1"),
    )
    assert r.status_code == 200

    listing = client.get(
        f"/workspaces/{ws}/channels/{general}/messages",
        headers=founder_headers(client, "w1"),
    )
    assert listing.status_code == 200
    rendered = [m for m in listing.json() if m["Message"]["message_id"] == message_id]
    assert len(rendered) == 1
    assert (
        rendered[0]["Message"]["message_text"]
        == "hello from a member about to be removed"
    )
    assert rendered[0]["Sender"] == {
        "member_id": None,
        "member_name": "(removed member)",
    }

    export = client.get(
        f"/workspaces/{ws}/export", headers=founder_headers(client, "w1")
    ).json()
    exported_messages = [
        m
        for m in export["messages"][general]
        if m["Message"]["message_id"] == message_id
    ]
    assert len(exported_messages) == 1
    assert exported_messages[0]["Sender"]["member_name"] == "(removed member)"


def test_history_survives_removal_of_a_mentioned_member(client):
    """Being MENTIONED (not just having posted) must not block removal --
    Mention.mentioned_member_id is nullable for the same reason."""
    ws, target = _ws_and_target(client)
    target_handle = target.get("handle")
    if target_handle is None:
        target_handle = client.get(
            "/members/me", headers=member_headers(client, "target", "w1")
        ).json()["handle"]

    channels = client.get(
        f"/workspaces/{ws}/channels", headers=founder_headers(client, "w1")
    ).json()
    general = [c for c in channels if c["channel_name"] == "general"][0]["channel_id"]

    posted = client.post(
        f"/workspaces/{ws}/channels/{general}/messages",
        json={"message_text": f"hey @{target_handle} welcome"},
        headers=founder_headers(client, "w1"),
    )
    assert posted.status_code == 200

    r = client.delete(
        f"/workspaces/{ws}/members/{target['member_id']}",
        headers=founder_headers(client, "w1"),
    )
    assert r.status_code == 200

    listing = client.get(
        f"/workspaces/{ws}/channels/{general}/messages",
        headers=founder_headers(client, "w1"),
    )
    assert listing.status_code == 200
