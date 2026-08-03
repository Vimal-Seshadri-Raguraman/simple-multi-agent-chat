"""E2E: a member goes away, the workspace keeps talking, they catch up."""

from tests.conftest import (
    founder_auth,
    founder_headers,
    general_channel_id,
    member_auth,
    member_headers,
)


def test_away_and_catch_up_journey(client):
    # A workspace with a founder and a member; the member "goes away".
    ws = founder_auth(client, "w1")["workspace_id"]
    general = general_channel_id(client, "w1")
    member_auth(client, "m2", "w1")

    # While away: 3 messages land in general, one of them mentions m2.
    for text in ["morning update", "@tm2 need your eyes on this", "wrapping up"]:
        response = client.post(
            f"/workspaces/{ws}/channels/{general}/messages",
            json={"message_text": text},
            headers=founder_headers(client, "w1"),
        )
        assert response.status_code == 200, response.text

    # Catch-up call: one request tells m2 everything they need.
    rows = client.get(
        f"/workspaces/{ws}/unreads", headers=member_headers(client, "m2", "w1")
    ).json()["unreads"]
    general_row = next(r for r in rows if r["channel_id"] == general)
    assert general_row["unread_count"] == 3
    assert general_row["mention_count"] == 1
    anchor = general_row["first_unread_message_id"]

    # Resume reading from the anchor with the EXISTING message pagination:
    # the anchor is the first unread, so page from just before it by
    # fetching without a cursor and slicing — or simply fetch the channel
    # and assert the anchor is present; then read everything.
    fetched = client.get(
        f"/workspaces/{ws}/channels/{general}/messages",
        headers=member_headers(client, "m2", "w1"),
    ).json()
    assert any(m["Message"]["message_id"] == anchor for m in fetched)

    # Reading did NOT mark anything: unreads unchanged (explicit model).
    rows = client.get(
        f"/workspaces/{ws}/unreads", headers=member_headers(client, "m2", "w1")
    ).json()["unreads"]
    assert next(r for r in rows if r["channel_id"] == general)["unread_count"] == 3

    # Mark read: bold clears; the red badge (mention) survives until ack.
    row = client.post(
        f"/workspaces/{ws}/channels/{general}/read",
        headers=member_headers(client, "m2", "w1"),
    ).json()
    assert row["unread_count"] == 0
    assert row["mention_count"] == 1

    # Ack via the inbox: fully caught up.
    events = client.get("/mentions", headers=member_headers(client, "m2", "w1")).json()
    client.post(
        f"/mentions/{events[0]['mention_id']}/ack",
        headers=member_headers(client, "m2", "w1"),
    )
    row = client.post(
        f"/workspaces/{ws}/channels/{general}/read",
        headers=member_headers(client, "m2", "w1"),
    ).json()
    assert row["mention_count"] == 0
