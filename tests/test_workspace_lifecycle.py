"""Workspace export and confirmed deletion with audit tombstone."""

import app.database as database_module
from app.models import Member, Mention, Workspace, WorkspaceRecord
from tests.conftest import founder_auth, founder_headers, member_auth, member_headers


def _seed(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    member_auth(client, "m2", "w1")
    channels = client.get(
        f"/workspaces/{ws}/channels", headers=founder_headers(client, "w1")
    ).json()
    general = channels[0]["channel_id"]
    client.post(
        f"/workspaces/{ws}/channels/{general}/messages",
        json={"message_text": "hello"},
        headers=founder_headers(client, "w1"),
    )
    client.post(
        f"/workspaces/{ws}/invites",
        json={"invite_type": "code"},
        headers=founder_headers(client, "w1"),
    )
    return ws


def test_export_admin_only_and_email_free(client):
    ws = _seed(client)
    r = client.get(
        f"/workspaces/{ws}/export", headers=member_headers(client, "m2", "w1")
    )
    assert r.status_code == 403
    dump = client.get(
        f"/workspaces/{ws}/export", headers=founder_headers(client, "w1")
    ).json()
    assert dump["workspace"]["workspace_id"] == ws
    assert len(dump["channels"]) == 1
    assert len(dump["members"]) == 2
    assert all("email" not in m for m in dump["members"])
    assert any(msgs for msgs in dump["messages"].values())
    assert len(dump["pending_invites"]) == 1


def test_delete_requires_exact_confirmation(client):
    ws = _seed(client)
    for bad in (None, "Delete", "w1-workspace"):
        params = {} if bad is None else {"confirm": bad}
        r = client.delete(
            f"/workspaces/{ws}", params=params, headers=founder_headers(client, "w1")
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "confirmation_required"
    # nothing was touched:
    with database_module.SessionLocal() as db:
        assert db.get(Workspace, ws) is not None


def test_delete_cascades_and_updates_tombstone(client):
    ws = _seed(client)
    founder_id = founder_auth(client, "w1")["member_id"]
    r = client.delete(
        f"/workspaces/{ws}",
        params={"confirm": "delete"},
        headers=founder_headers(client, "w1"),
    )
    assert r.status_code == 200 and r.json() == {"status": "deleted"}
    with database_module.SessionLocal() as db:
        assert db.get(Workspace, ws) is None
        assert db.query(Member).filter(Member.workspace_id == ws).count() == 0
        record = db.get(WorkspaceRecord, ws)
        assert record.status == "deleted"
        assert record.deleted_by == founder_id
        assert record.deleted_at is not None
    # dead tokens: the founder's bearer no longer works anywhere
    r = client.get(f"/workspaces/{ws}/members", headers=founder_headers(client, "w1"))
    assert r.status_code == 401


def test_delete_cascades_mentions_from_a_real_mention(client):
    # Regression coverage for the mentions-before-messages cascade order:
    # unlike _seed's plain "hello" message, this one produces an actual
    # Mention row (m2's handle is "tm2" -- see app/handles.py slugify), so
    # the delete's `Mention` cleanup step is actually exercised with rows
    # present, not a no-op on an empty table.
    ws = _seed(client)
    channels = client.get(
        f"/workspaces/{ws}/channels", headers=founder_headers(client, "w1")
    ).json()
    general = channels[0]["channel_id"]
    posted = client.post(
        f"/workspaces/{ws}/channels/{general}/messages",
        json={"message_text": "@tm2 please review"},
        headers=founder_headers(client, "w1"),
    ).json()
    assert posted["mentions"], "expected a real mention row to be created"

    r = client.delete(
        f"/workspaces/{ws}",
        params={"confirm": "delete"},
        headers=founder_headers(client, "w1"),
    )
    assert r.status_code == 200 and r.json() == {"status": "deleted"}
    with database_module.SessionLocal() as db:
        assert db.get(Workspace, ws) is None
        assert (
            db.query(Mention)
            .filter(Mention.message_id == posted["Message"]["message_id"])
            .count()
            == 0
        )


def test_delete_admin_only(client):
    ws = _seed(client)
    r = client.delete(
        f"/workspaces/{ws}",
        params={"confirm": "delete"},
        headers=member_headers(client, "m2", "w1"),
    )
    assert r.status_code == 403
