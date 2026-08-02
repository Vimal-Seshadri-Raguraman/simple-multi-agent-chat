"""End-to-end onboarding journeys: email invite and shareable code."""

from tests.conftest import human_headers, human_member_id


def test_full_email_invite_journey(client):
    # Host registers and creates a workspace (general auto-created, host inside).
    host = human_headers(client, "host")
    ws = client.post(
        "/workspaces", json={"workspace_name": "Team"}, headers=host
    ).json()

    # Host invites an address that has NO account yet.
    invite = client.post(
        f"/workspaces/{ws['workspace_id']}/invites",
        json={"invite_type": "email", "email": "newbie@test.example"},
        headers=host,
    ).json()

    # The future member registers with that email and discovers the invite.
    newbie = human_headers(client, "newbie")  # registers newbie@test.example
    pending = client.get("/invites", headers=newbie).json()
    assert [p["invite_id"] for p in pending] == [invite["invite_id"]]
    assert pending[0]["workspace"]["workspace_name"] == "Team"

    # Accept → in the workspace and the default channel; can post immediately.
    client.post(f"/invites/{invite['invite_id']}/accept", headers=newbie)
    channels = client.get(
        f"/workspaces/{ws['workspace_id']}/channels", headers=newbie
    ).json()
    general = [c for c in channels if c["channel_name"] == "general"][0]
    message = client.post(
        f"/workspaces/{ws['workspace_id']}/channels/{general['channel_id']}/messages",
        json={"message_text": "Hi, I just joined!"},
        headers=newbie,
    )
    assert message.status_code == 200
    assert message.json()["Sender"]["member_id"] == human_member_id(client, "newbie")


def test_full_code_journey(client):
    host = human_headers(client, "host")
    ws = client.post(
        "/workspaces", json={"workspace_name": "Team"}, headers=host
    ).json()
    code = client.post(
        f"/workspaces/{ws['workspace_id']}/invites",
        json={"invite_type": "code"},
        headers=host,
    ).json()["code"]

    # Two teammates join with the same code and can both post in general.
    for key in ("dev1", "dev2"):
        member = human_headers(client, key)
        joined = client.post("/workspaces/join", json={"code": code}, headers=member)
        assert joined.status_code == 200
        channels = client.get(
            f"/workspaces/{ws['workspace_id']}/channels", headers=member
        ).json()
        general = [c for c in channels if c["channel_name"] == "general"][0]
        posted = client.post(
            f"/workspaces/{ws['workspace_id']}/channels/{general['channel_id']}/messages",
            json={"message_text": f"{key} checking in"},
            headers=member,
        )
        assert posted.status_code == 200

    # Host revokes; a third teammate is locked out.
    invites = client.get(
        f"/workspaces/{ws['workspace_id']}/invites", headers=host
    ).json()
    client.delete(
        f"/workspaces/{ws['workspace_id']}/invites/{invites[0]['invite_id']}",
        headers=host,
    )
    late = client.post(
        "/workspaces/join", json={"code": code}, headers=human_headers(client, "dev3")
    )
    assert late.status_code == 404
