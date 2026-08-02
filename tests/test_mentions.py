"""@handle mention parsing/canonicalization and #channel reference resolution."""

import pytest
from starlette.websockets import WebSocketDisconnect

import app.database as database_module
from app.models import Mention
from tests.conftest import (
    founder_auth,
    founder_headers,
    general_channel_id,
    member_auth,
    member_headers,
    member_token,
)


def _setup(client):
    """Found w1, register m2 into it, fetch general's id.

    Returns (workspace_id, general_channel_id, m2_member_id). m2's handle is
    "tm2" (first_name="Test", last_name="m2" -> slugify("Tm2") -> "tm2").
    """
    ws = founder_auth(client, "w1")["workspace_id"]
    general = general_channel_id(client, "w1")
    m2 = member_auth(client, "m2", "w1")["member_id"]
    return ws, general, m2


def me_handle(client):
    """The founder's own handle, fetched via GET /member?id=<self>."""
    founder = founder_auth(client, "w1")
    response = client.get(
        f"/member?id={founder['member_id']}", headers=founder_headers(client, "w1")
    )
    assert response.status_code == 200, response.text
    return response.json()["handle"]


def test_typed_handle_stored_as_id_token(client):
    ws, general, m2 = _setup(client)  # m2's handle is "tm2" (Test m2 -> t+m2)
    posted = client.post(
        f"/workspaces/{ws}/channels/{general}/messages",
        json={"message_text": "@tm2 can you check this?"},
        headers=founder_headers(client, "w1"),
    ).json()
    m2_id = member_auth(client, "m2", "w1")["member_id"]
    assert posted["Message"]["message_text"] == f"<@{m2_id}> can you check this?"
    assert posted["mentions"] == [
        {"member_id": m2_id, "handle": "tm2", "member_name": "Test m2"}
    ]


def test_unresolved_handle_left_alone(client):
    ws, general, _ = _setup(client)
    posted = client.post(
        f"/workspaces/{ws}/channels/{general}/messages",
        json={"message_text": "email me @ghost or ping @nobody-here"},
        headers=founder_headers(client, "w1"),
    ).json()
    assert "@ghost" in posted["Message"]["message_text"]
    assert posted["mentions"] == []


def test_duplicate_and_self_mentions(client):
    ws, general, m2 = _setup(client)
    founder_id = founder_auth(client, "w1")["member_id"]
    posted = client.post(
        f"/workspaces/{ws}/channels/{general}/messages",
        json={"message_text": f"@tm2 @tm2 and @{me_handle(client)} too"},
        headers=founder_headers(client, "w1"),
    ).json()
    assert len(posted["mentions"]) == 1  # deduped, self excluded

    # Assert directly against the DB, not just the payload: the payload's
    # `mentions` array has the sender's own id filtered out by
    # build_message_payload regardless of what actually got written, so it
    # can't catch a regression in canonicalize's self-exclusion guard that
    # started inserting a Mention row for the sender. Task 4 fans out events
    # from Mention rows directly, so a masked self-mention row would
    # self-notify -- this must be checked at the row level.
    with database_module.SessionLocal() as db:
        rows = (
            db.query(Mention)
            .filter(Mention.message_id == posted["Message"]["message_id"])
            .all()
        )
        assert len(rows) == 1
        assert rows[0].mentioned_member_id == m2
        assert all(r.mentioned_member_id != founder_id for r in rows)


def test_channel_ref_resolves_as_link_only(client):
    ws, general, _ = _setup(client)
    client.post(
        f"/workspaces/{ws}/channels",
        json={"channel_name": "reports"},
        headers=founder_headers(client, "w1"),
    )
    posted = client.post(
        f"/workspaces/{ws}/channels/{general}/messages",
        json={"message_text": "see #reports and #nonexistent"},
        headers=founder_headers(client, "w1"),
    ).json()
    assert [c["channel_name"] for c in posted["channel_refs"]] == ["reports"]
    assert posted["Message"]["message_text"] == "see #reports and #nonexistent"


def test_rename_reflected_at_read_time(client):
    ws, general, m2 = _setup(client)
    client.post(
        f"/workspaces/{ws}/channels/{general}/messages",
        json={"message_text": "@tm2 hello"},
        headers=founder_headers(client, "w1"),
    )
    client.patch(
        "/members/me",
        json={"handle": "newname"},
        headers=member_headers(client, "m2", "w1"),
    )
    fetched = client.get(
        f"/workspaces/{ws}/channels/{general}/messages",
        headers=founder_headers(client, "w1"),
    ).json()
    assert fetched[-1]["mentions"][0]["handle"] == "newname"  # stored ID, live handle


def test_event_socket_delivers_mention_cross_channel(client):
    """A member's own event socket receives a mention even when the message

    is posted in a channel the mentioned member is NOT part of -- proving
    live mention push is independent of channel membership/broadcast.
    """
    ws = founder_auth(client, "w1")["workspace_id"]
    general = general_channel_id(client, "w1")
    # An agent registered via /members/agents is never auto-joined to any
    # channel, so it is provably not a member of "general".
    agent = client.post(
        "/members/agents",
        json={"member_name": "Bot"},
        headers=founder_headers(client, "w1"),
    ).json()
    events_url = f"/ws/workspaces/{ws}/members/me/events?token={agent['api_key']}"

    with client.websocket_connect(events_url) as ws_events:
        client.post(
            f"/workspaces/{ws}/channels/{general}/messages",
            json={"message_text": f"@{agent['handle']} please take a look"},
            headers=founder_headers(client, "w1"),
        )
        event = ws_events.receive_json()

    assert event["event"] == "mention"
    assert event["mentioned_member_id"] == agent["member_id"]
    assert f"<@{agent['member_id']}>" in event["message"]["Message"]["message_text"]


def test_event_socket_wall_cross_workspace_rejection(client):
    """A member of a foreign workspace cannot connect to this workspace's

    events path -- same 4404 wall as the channel socket
    (test_websocket_wall_cross_workspace_rejection).
    """
    ws_a = founder_auth(client, "wa")["workspace_id"]
    intruder_token = member_token(client, "intruder", workspace_key="wb")
    events_url = f"/ws/workspaces/{ws_a}/members/me/events?token={intruder_token}"

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(events_url):
            pass
    assert exc_info.value.code == 4404
