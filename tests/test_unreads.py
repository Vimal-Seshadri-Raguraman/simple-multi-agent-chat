"""Read cursors: caught-up-at-join semantics and the unreads surface."""

from tests.conftest import (
    founder_auth,
    founder_headers,
    general_channel_id,
    member_auth,
    member_headers,
)


def _post(client, ws, channel, key, text):
    response = client.post(
        f"/workspaces/{ws}/channels/{channel}/messages",
        json={"message_text": text},
        headers=(
            founder_headers(client, key)
            if key == "w1"
            else member_headers(client, key, "w1")
        ),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_join_default_channel_starts_caught_up(client):
    """A member registering into a workspace with prior general-channel
    history must start with last_read_seq == the channel's max seq."""
    ws = founder_auth(client, "w1")["workspace_id"]
    general = general_channel_id(client, "w1")
    _post(client, ws, general, "w1", "history before m2 joined")
    member_auth(client, "m2", "w1")  # joins general via the default-channel path

    import app.database as database_module
    from app.models import ChannelMember, Message

    with database_module.SessionLocal() as db:
        m2_id = member_auth(client, "m2", "w1")["member_id"]
        row = db.get(ChannelMember, (general, m2_id))
        max_seq = (
            db.query(Message.seq)
            .filter(Message.channel_id == general)
            .order_by(Message.seq.desc())
            .first()
        )[0]
        assert row.last_read_seq == max_seq


def test_explicit_channel_add_starts_caught_up(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    channel = client.post(
        f"/workspaces/{ws}/channels",
        json={"channel_name": "reports"},
        headers=founder_headers(client, "w1"),
    ).json()["channel_id"]
    # Founder must be a channel member to post: add founder, post, then add m2.
    founder_id = founder_auth(client, "w1")["member_id"]
    client.post(
        f"/workspaces/{ws}/channels/{channel}/members",
        json={"member_id": founder_id},
        headers=founder_headers(client, "w1"),
    )
    _post(client, ws, channel, "w1", "pre-existing message")
    m2_id = member_auth(client, "m2", "w1")["member_id"]
    client.post(
        f"/workspaces/{ws}/channels/{channel}/members",
        json={"member_id": m2_id},
        headers=founder_headers(client, "w1"),
    )

    import app.database as database_module
    from app.models import ChannelMember

    with database_module.SessionLocal() as db:
        assert db.get(ChannelMember, (channel, m2_id)).last_read_seq == 1


def test_latest_seq_empty_channel_is_zero(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    channel = client.post(
        f"/workspaces/{ws}/channels",
        json={"channel_name": "empty"},
        headers=founder_headers(client, "w1"),
    ).json()["channel_id"]

    import app.database as database_module
    from app.unreads import latest_seq

    with database_module.SessionLocal() as db:
        assert latest_seq(db, channel) == 0


def test_unreads_lifecycle_counts_and_anchor(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    general = general_channel_id(client, "w1")
    member_auth(client, "m2", "w1")
    first = _post(client, ws, general, "w1", "one")
    _post(client, ws, general, "w1", "two")

    rows = client.get(
        f"/workspaces/{ws}/unreads", headers=member_headers(client, "m2", "w1")
    ).json()["unreads"]
    general_row = next(r for r in rows if r["channel_id"] == general)
    assert general_row["unread_count"] == 2
    assert general_row["first_unread_message_id"] == first["Message"]["message_id"]
    assert general_row["mention_count"] == 0
    assert general_row["channel_name"] == "general"


def test_unreads_includes_caught_up_channels(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    general = general_channel_id(client, "w1")
    rows = client.get(
        f"/workspaces/{ws}/unreads", headers=founder_headers(client, "w1")
    ).json()["unreads"]
    general_row = next(r for r in rows if r["channel_id"] == general)
    assert general_row["unread_count"] == 0
    assert general_row["first_unread_message_id"] is None


def test_unreads_lists_only_my_channels(client):
    """A channel the caller is NOT a member of never appears."""
    ws = founder_auth(client, "w1")["workspace_id"]
    client.post(
        f"/workspaces/{ws}/channels",
        json={"channel_name": "private-ish"},
        headers=founder_headers(client, "w1"),
    )
    member_auth(client, "m2", "w1")
    rows = client.get(
        f"/workspaces/{ws}/unreads", headers=member_headers(client, "m2", "w1")
    ).json()["unreads"]
    assert [r["channel_name"] for r in rows] == ["general"]


def test_unreads_mention_count_independent_of_reads(client):
    """An unacked mention shows in mention_count even in a fully-read
    channel; unread_count and mention_count move independently."""
    ws = founder_auth(client, "w1")["workspace_id"]
    general = general_channel_id(client, "w1")
    m2 = member_auth(client, "m2", "w1")
    _post(client, ws, general, "w1", "@tm2 look at this")  # m2's handle: tm2

    rows = client.get(
        f"/workspaces/{ws}/unreads", headers=member_headers(client, "m2", "w1")
    ).json()["unreads"]
    general_row = next(r for r in rows if r["channel_id"] == general)
    assert general_row["unread_count"] == 1
    assert general_row["mention_count"] == 1

    # Ack the mention via the inbox: mention_count drops, unread_count stays.
    events = client.get("/mentions", headers=member_headers(client, "m2", "w1")).json()
    client.post(
        f"/mentions/{events[0]['mention_id']}/ack",
        headers=member_headers(client, "m2", "w1"),
    )
    rows = client.get(
        f"/workspaces/{ws}/unreads", headers=member_headers(client, "m2", "w1")
    ).json()["unreads"]
    general_row = next(r for r in rows if r["channel_id"] == general)
    assert general_row["unread_count"] == 1
    assert general_row["mention_count"] == 0


def test_unreads_foreign_workspace_uniform_404(client):
    founder_auth(client, "w1")
    other_ws = founder_auth(client, "w2")["workspace_id"]
    response = client.get(
        f"/workspaces/{other_ws}/unreads", headers=founder_headers(client, "w1")
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unreads_accessible_via_agent_api_key(client):
    """Agents authenticate with X-API-Key; get_current_member accepts it here too."""
    ws = founder_auth(client, "w1")["workspace_id"]
    general = general_channel_id(client, "w1")
    agent = client.post(
        "/members/agents",
        json={"member_name": "Bot1"},
        headers=founder_headers(client, "w1"),
    ).json()
    client.post(
        f"/workspaces/{ws}/channels/{general}/members",
        json={"member_id": agent["member_id"]},
        headers=founder_headers(client, "w1"),
    )
    response = client.get(
        f"/workspaces/{ws}/unreads", headers={"X-API-Key": agent["api_key"]}
    )
    assert response.status_code == 200
    assert response.json()["unreads"][0]["channel_name"] == "general"
