"""Tests for workspace invites: creation, listing, revocation."""

from tests.conftest import human_headers, human_member_id


def _workspace(client, key="m_1"):
    return client.post(
        "/workspaces",
        json={"workspace_name": "Acme"},
        headers=human_headers(client, key),
    ).json()


def test_create_email_invite(client):
    ws = _workspace(client)
    response = client.post(
        f"/workspaces/{ws['workspace_id']}/invites",
        json={"invite_type": "email", "email": "Alice@Test.Example"},
        headers=human_headers(client, "m_1"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["invite_type"] == "email"
    assert body["email"] == "alice@test.example"  # lowercased
    assert body["code"] is None
    assert body["expires_at"] is None


def test_create_code_invite(client):
    ws = _workspace(client)
    response = client.post(
        f"/workspaces/{ws['workspace_id']}/invites",
        json={"invite_type": "code"},
        headers=human_headers(client, "m_1"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["invite_type"] == "code"
    assert isinstance(body["code"], str) and len(body["code"]) >= 10
    assert body["email"] is None
    assert body["expires_at"] is not None


def test_email_invite_requires_email_field(client):
    ws = _workspace(client)
    response = client.post(
        f"/workspaces/{ws['workspace_id']}/invites",
        json={"invite_type": "email"},
        headers=human_headers(client, "m_1"),
    )
    assert response.status_code == 422


def test_duplicate_pending_email_invite_conflicts(client):
    ws = _workspace(client)
    body = {"invite_type": "email", "email": "alice@test.example"}
    url = f"/workspaces/{ws['workspace_id']}/invites"
    client.post(url, json=body, headers=human_headers(client, "m_1"))
    response = client.post(url, json=body, headers=human_headers(client, "m_1"))
    assert response.status_code == 409


def test_inviting_existing_member_conflicts(client):
    ws = _workspace(client)
    human_member_id(client, "m_2")  # registers m_2@test.example
    client.post(
        f"/workspaces/{ws['workspace_id']}/members",
        json={"member_id": human_member_id(client, "m_2")},
        headers=human_headers(client, "m_1"),
    )
    response = client.post(
        f"/workspaces/{ws['workspace_id']}/invites",
        json={"invite_type": "email", "email": "m_2@test.example"},
        headers=human_headers(client, "m_1"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_a_member"


def test_list_and_revoke_invites(client):
    ws = _workspace(client)
    url = f"/workspaces/{ws['workspace_id']}/invites"
    client.post(
        url,
        json={"invite_type": "email", "email": "alice@test.example"},
        headers=human_headers(client, "m_1"),
    )
    code_invite = client.post(
        url, json={"invite_type": "code"}, headers=human_headers(client, "m_1")
    ).json()

    listing = client.get(url, headers=human_headers(client, "m_1")).json()
    assert len(listing) == 2
    assert code_invite["code"] in [i["code"] for i in listing]  # re-viewable

    revoke = client.delete(
        f"{url}/{code_invite['invite_id']}", headers=human_headers(client, "m_1")
    )
    assert revoke.status_code == 200
    assert len(client.get(url, headers=human_headers(client, "m_1")).json()) == 1


def test_revoke_unknown_invite_404(client):
    ws = _workspace(client)
    response = client.delete(
        f"/workspaces/{ws['workspace_id']}/invites/nope",
        headers=human_headers(client, "m_1"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "invalid_invite"


def test_non_member_cannot_create_invite(client):
    ws = _workspace(client)
    response = client.post(
        f"/workspaces/{ws['workspace_id']}/invites",
        json={"invite_type": "code"},
        headers=human_headers(client, "outsider"),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_a_member"


def test_agent_cannot_create_invite(client):
    ws = _workspace(client)
    agent = client.post(
        "/members/agents",
        json={"member_name": "Bot"},
        headers=human_headers(client, "m_1"),
    ).json()
    response = client.post(
        f"/workspaces/{ws['workspace_id']}/invites",
        json={"invite_type": "code"},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden_member_type"


def _invite_email(client, ws_id, email, key="m_1"):
    return client.post(
        f"/workspaces/{ws_id}/invites",
        json={"invite_type": "email", "email": email},
        headers=human_headers(client, key),
    ).json()


def test_invitee_sees_pending_invite_with_context(client):
    ws = _workspace(client)
    _invite_email(client, ws["workspace_id"], "m_2@test.example")
    listing = client.get("/invites", headers=human_headers(client, "m_2")).json()
    assert len(listing) == 1
    row = listing[0]
    assert row["workspace"]["workspace_name"] == "Acme"
    assert row["invited_by"]["member_id"] == human_member_id(client, "m_1")


def test_accept_joins_workspace_and_default_channel(client):
    ws = _workspace(client)
    invite = _invite_email(client, ws["workspace_id"], "m_2@test.example")
    response = client.post(
        f"/invites/{invite['invite_id']}/accept", headers=human_headers(client, "m_2")
    )
    assert response.status_code == 200
    assert response.json()["workspace_id"] == ws["workspace_id"]

    members = client.get(
        f"/workspaces/{ws['workspace_id']}/members",
        headers=human_headers(client, "m_2"),
    ).json()
    assert human_member_id(client, "m_2") in [m["member_id"] for m in members]

    channels = client.get(
        f"/workspaces/{ws['workspace_id']}/channels",
        headers=human_headers(client, "m_2"),
    ).json()
    general_id = [c for c in channels if c["channel_name"] == "general"][0][
        "channel_id"
    ]
    channel_members = client.get(
        f"/workspaces/{ws['workspace_id']}/channels/{general_id}/members",
        headers=human_headers(client, "m_2"),
    ).json()
    assert human_member_id(client, "m_2") in [m["member_id"] for m in channel_members]

    # Consumed: no longer listed, second accept 404s.
    assert client.get("/invites", headers=human_headers(client, "m_2")).json() == []
    replay = client.post(
        f"/invites/{invite['invite_id']}/accept", headers=human_headers(client, "m_2")
    )
    assert replay.status_code == 404
    assert replay.json()["error"]["code"] == "invalid_invite"


def test_cannot_accept_someone_elses_invite(client):
    ws = _workspace(client)
    invite = _invite_email(client, ws["workspace_id"], "m_2@test.example")
    response = client.post(
        f"/invites/{invite['invite_id']}/accept", headers=human_headers(client, "m_3")
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "invalid_invite"


def test_decline_removes_without_joining(client):
    ws = _workspace(client)
    invite = _invite_email(client, ws["workspace_id"], "m_2@test.example")
    response = client.post(
        f"/invites/{invite['invite_id']}/decline", headers=human_headers(client, "m_2")
    )
    assert response.status_code == 200
    assert response.json() == {"status": "declined"}
    assert client.get("/invites", headers=human_headers(client, "m_2")).json() == []
    listing = client.get(
        f"/workspaces/{ws['workspace_id']}/members",
        headers=human_headers(client, "m_1"),
    ).json()
    assert human_member_id(client, "m_2") not in [m["member_id"] for m in listing]


def test_accept_when_already_member_deletes_invite_and_409s(client):
    ws = _workspace(client)
    invite = _invite_email(client, ws["workspace_id"], "m_2@test.example")
    client.post(
        f"/workspaces/{ws['workspace_id']}/members",
        json={"member_id": human_member_id(client, "m_2")},
        headers=human_headers(client, "m_1"),
    )
    response = client.post(
        f"/invites/{invite['invite_id']}/accept", headers=human_headers(client, "m_2")
    )
    assert response.status_code == 409
    assert client.get("/invites", headers=human_headers(client, "m_2")).json() == []
