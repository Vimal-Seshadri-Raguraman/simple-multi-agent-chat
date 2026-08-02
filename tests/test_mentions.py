"""@handle mention parsing/canonicalization and #channel reference resolution."""

from tests.conftest import (
    founder_auth,
    founder_headers,
    general_channel_id,
    member_auth,
    member_headers,
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
    posted = client.post(
        f"/workspaces/{ws}/channels/{general}/messages",
        json={"message_text": f"@tm2 @tm2 and @{me_handle(client)} too"},
        headers=founder_headers(client, "w1"),
    ).json()
    assert len(posted["mentions"]) == 1  # deduped, self excluded


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
