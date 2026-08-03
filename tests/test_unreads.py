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
