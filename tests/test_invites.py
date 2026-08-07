"""Tests for workspace invites: creation, listing, revocation, and redemption."""

from tests.conftest import founder_auth, founder_headers, member_auth

_PASSWORD = "test-password-123"  # matches conftest._TEST_PASSWORD


def _account_headers(client, email: str) -> dict[str, str]:
    """Create a fresh account and return account-tier Bearer headers for
    it -- the join/register doors are account-authed (spec §3)."""
    response = client.post("/accounts", json={"email": email, "password": _PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


def test_create_email_invite(client):
    founder = founder_auth(client, "w1")
    response = client.post(
        f"/workspaces/{founder['workspace_id']}/invites",
        json={"invite_type": "email", "email": "Alice@Test.Example"},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["invite_type"] == "email"
    assert body["email"] == "alice@test.example"  # lowercased
    assert body["code"] is None
    assert body["expires_at"] is None


def test_create_code_invite(client):
    founder = founder_auth(client, "w1")
    response = client.post(
        f"/workspaces/{founder['workspace_id']}/invites",
        json={"invite_type": "code"},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["invite_type"] == "code"
    assert isinstance(body["code"], str) and len(body["code"]) >= 10
    assert body["email"] is None
    assert body["expires_at"] is not None


def test_email_invite_requires_email_field(client):
    founder = founder_auth(client, "w1")
    response = client.post(
        f"/workspaces/{founder['workspace_id']}/invites",
        json={"invite_type": "email"},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 422


def test_duplicate_pending_email_invite_conflicts(client):
    founder = founder_auth(client, "w1")
    body = {"invite_type": "email", "email": "alice@test.example"}
    url = f"/workspaces/{founder['workspace_id']}/invites"
    client.post(url, json=body, headers=founder_headers(client, "w1"))
    response = client.post(url, json=body, headers=founder_headers(client, "w1"))
    assert response.status_code == 409


def test_inviting_existing_member_conflicts(client):
    founder = founder_auth(client, "w1")
    member_auth(client, "m2", "w1")  # registers m2@test.example into w1
    response = client.post(
        f"/workspaces/{founder['workspace_id']}/invites",
        json={"invite_type": "email", "email": "m2@test.example"},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_a_member"


def test_list_and_revoke_invites(client):
    founder = founder_auth(client, "w1")
    url = f"/workspaces/{founder['workspace_id']}/invites"
    headers = founder_headers(client, "w1")
    client.post(
        url,
        json={"invite_type": "email", "email": "alice@test.example"},
        headers=headers,
    )
    code_invite = client.post(url, json={"invite_type": "code"}, headers=headers).json()

    listing = client.get(url, headers=headers).json()
    assert len(listing) == 2
    assert code_invite["code"] in [i["code"] for i in listing]  # re-viewable

    revoke = client.delete(f"{url}/{code_invite['invite_id']}", headers=headers)
    assert revoke.status_code == 200
    assert len(client.get(url, headers=headers).json()) == 1


def test_revoke_unknown_invite_404(client):
    founder = founder_auth(client, "w1")
    response = client.delete(
        f"/workspaces/{founder['workspace_id']}/invites/nope",
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "invalid_invite"


def test_foreign_workspace_member_cannot_create_invite(client):
    """A founder of a different workspace hits the wall, same as a stranger would."""
    founder = founder_auth(client, "w1")
    response = client.post(
        f"/workspaces/{founder['workspace_id']}/invites",
        json={"invite_type": "code"},
        headers=founder_headers(client, "w2"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_agent_cannot_create_invite(client):
    """SMAC-92: create_invite is now capability-gated (`Cap.MINT_HUMAN_INVITES`
    for email/code), not just human-type-gated -- an agent's type cap
    intersects it away regardless of role, so this is now the generic
    `forbidden` envelope, not `forbidden_member_type`."""
    founder = founder_auth(client, "w1")
    agent = client.post(
        "/members/agents",
        json={"member_name": "Bot"},
        headers=founder_headers(client, "w1"),
    ).json()
    response = client.post(
        f"/workspaces/{founder['workspace_id']}/invites",
        json={"invite_type": "code"},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def _code_invite(client, workspace_id, key="w1"):
    return client.post(
        f"/workspaces/{workspace_id}/invites",
        json={"invite_type": "code"},
        headers=founder_headers(client, key),
    ).json()


def _code_register_body(code: str, first_name: str, last_name: str) -> dict:
    return {"code": code, "first_name": first_name, "last_name": last_name}


def test_code_is_multi_use(client):
    founder = founder_auth(client, "w1")
    invite = _code_invite(client, founder["workspace_id"])
    for key in ("dev1", "dev2"):
        response = client.post(
            "/workspaces/join",
            json=_code_register_body(invite["code"], "Dev", key),
            headers=_account_headers(client, f"{key}@test.example"),
        )
        assert response.status_code == 200
        assert response.json()["workspace"]["workspace_id"] == founder["workspace_id"]

    members = client.get(
        f"/workspaces/{founder['workspace_id']}/members",
        headers=founder_headers(client, "w1"),
    ).json()
    assert len(members) == 3  # founder + two code registrants


def test_joiner_lands_in_default_channel(client):
    founder = founder_auth(client, "w1")
    invite = _code_invite(client, founder["workspace_id"])
    joined = client.post(
        "/workspaces/join",
        json=_code_register_body(invite["code"], "Joi", "Ner"),
        headers=_account_headers(client, "joiner@test.example"),
    ).json()
    joiner_id = joined["member"]["member_id"]
    joiner_headers = {"Authorization": f"Bearer {joined['access_token']}"}

    channels = client.get(
        f"/workspaces/{founder['workspace_id']}/channels", headers=joiner_headers
    ).json()
    general_id = [c for c in channels if c["channel_name"] == "general"][0][
        "channel_id"
    ]
    channel_members = client.get(
        f"/workspaces/{founder['workspace_id']}/channels/{general_id}/members",
        headers=joiner_headers,
    ).json()
    assert joiner_id in [m["member_id"] for m in channel_members]


def test_revoked_code_rejected(client):
    founder = founder_auth(client, "w1")
    invite = _code_invite(client, founder["workspace_id"])
    client.delete(
        f"/workspaces/{founder['workspace_id']}/invites/{invite['invite_id']}",
        headers=founder_headers(client, "w1"),
    )
    response = client.post(
        "/workspaces/join",
        json=_code_register_body(invite["code"], "La", "Te"),
        headers=_account_headers(client, "late@test.example"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "invalid_invite"


def test_expired_code_rejected_and_deleted(client):
    from datetime import timedelta

    import app.database as database_module
    from app.models import WorkspaceInvite, utcnow

    founder = founder_auth(client, "w1")
    invite = _code_invite(client, founder["workspace_id"])
    with database_module.SessionLocal() as db:
        row = db.get(WorkspaceInvite, invite["invite_id"])
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.add(row)
        db.commit()

    response = client.post(
        "/workspaces/join",
        json=_code_register_body(invite["code"], "La", "Te"),
        headers=_account_headers(client, "late@test.example"),
    )
    assert response.status_code == 404
    with database_module.SessionLocal() as db:
        assert db.get(WorkspaceInvite, invite["invite_id"]) is None


def test_unknown_code_rejected(client):
    response = client.post(
        "/workspaces/join",
        json=_code_register_body("not-a-real-code", "No", "Body"),
        headers=_account_headers(client, "nobody@test.example"),
    )
    assert response.status_code == 404


def test_reusing_code_with_same_account_conflicts(client):
    """The old "same email replays a code" leak is now "the same ACCOUNT
    replays a code" -- since there's only one profile per account per
    workspace now (uq_members_workspace_account), a second join attempt
    with the SAME account is already_a_member, not email_taken."""
    founder = founder_auth(client, "w1")
    invite = _code_invite(client, founder["workspace_id"])
    headers = _account_headers(client, "dev@test.example")
    body = _code_register_body(invite["code"], "De", "V")

    first = client.post("/workspaces/join", json=body, headers=headers)
    assert first.status_code == 200

    replay = client.post("/workspaces/join", json=body, headers=headers)
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "already_a_member"

    # Code stays valid for a different account (multi-use).
    other = client.post(
        "/workspaces/join",
        json=body,
        headers=_account_headers(client, "other@test.example"),
    )
    assert other.status_code == 200


def test_private_workspace_registration_with_reserved_seat_succeeds_and_consumes_it(
    client,
):
    founder = founder_auth(client, "w1", visibility="private")
    client.post(
        f"/workspaces/{founder['workspace_id']}/invites",
        json={"invite_type": "email", "email": "newbie@test.example"},
        headers=founder_headers(client, "w1", visibility="private"),
    )

    response = client.post(
        f"/workspaces/{founder['workspace_id']}/register",
        json={"first_name": "New", "last_name": "Bie"},
        headers=_account_headers(client, "newbie@test.example"),
    )
    assert response.status_code == 200
    assert response.json()["workspace"]["workspace_id"] == founder["workspace_id"]

    # Seat consumed: no longer listed.
    listing = client.get(
        f"/workspaces/{founder['workspace_id']}/invites",
        headers=founder_headers(client, "w1", visibility="private"),
    ).json()
    assert listing == []


def test_private_workspace_registration_without_seat_404s(client):
    founder = founder_auth(client, "w1", visibility="private")
    response = client.post(
        f"/workspaces/{founder['workspace_id']}/register",
        json={"first_name": "Stran", "last_name": "Ger"},
        headers=_account_headers(client, "stranger@test.example"),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_failed_registration_does_not_consume_seat(client):
    """A registration that fails after the seat is found must not burn it:
    seat deletion and profile creation are one transaction, not two.

    create_invite already blocks inviting an email whose account already
    has a member in this workspace, so the only way to reach
    _register_account with a seat AND a pre-existing membership for the
    same account is to simulate the race directly against the DB (a seat
    created for an email that -- by the time registration runs -- already
    belongs to a member).
    """
    import app.database as database_module
    from app.models import WorkspaceInvite

    founder = founder_auth(client, "w1", visibility="private")
    headers = founder_headers(client, "w1", visibility="private")
    racer_headers = _account_headers(client, "racer@test.example")

    # racer@test.example already has a member in this workspace.
    client.post(
        f"/workspaces/{founder['workspace_id']}/invites",
        json={"invite_type": "email", "email": "racer@test.example"},
        headers=headers,
    )
    client.post(
        f"/workspaces/{founder['workspace_id']}/register",
        json={"first_name": "Ra", "last_name": "Cer"},
        headers=racer_headers,
    )

    # Simulate the race: a stray seat for that same, now-taken email exists
    # (bypassing create_invite's existing-member check, which would normally
    # forbid this).
    with database_module.SessionLocal() as db:
        stray_seat = WorkspaceInvite(
            workspace_id=founder["workspace_id"],
            invite_type="email",
            email="racer@test.example",
            created_by=founder["member_id"],
        )
        db.add(stray_seat)
        db.commit()
        seat_id = stray_seat.invite_id

    response = client.post(
        f"/workspaces/{founder['workspace_id']}/register",
        json={"first_name": "Ra", "last_name": "Cer"},
        headers=racer_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_a_member"

    # The seat must have survived the failed registration.
    with database_module.SessionLocal() as db:
        assert db.get(WorkspaceInvite, seat_id) is not None


def test_code_registration_consumes_matching_email_seat(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    headers = founder_headers(client, "w1")
    client.post(
        f"/workspaces/{ws}/invites",
        json={"invite_type": "email", "email": "dual@test.example"},
        headers=headers,
    )
    code = client.post(
        f"/workspaces/{ws}/invites", json={"invite_type": "code"}, headers=headers
    ).json()["code"]

    joined = client.post(
        "/workspaces/join",
        json={"code": code, "first_name": "Du", "last_name": "Al"},
        headers=_account_headers(client, "DUAL@test.example"),
    )
    assert joined.status_code == 200
    remaining = client.get(f"/workspaces/{ws}/invites", headers=headers).json()
    # The email seat is gone (consumed by the code registration); the code survives.
    assert [i["invite_type"] for i in remaining] == ["code"]


def test_code_registration_leaves_other_seats_alone(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    headers = founder_headers(client, "w1")
    client.post(
        f"/workspaces/{ws}/invites",
        json={"invite_type": "email", "email": "someoneelse@test.example"},
        headers=headers,
    )
    code = client.post(
        f"/workspaces/{ws}/invites", json={"invite_type": "code"}, headers=headers
    ).json()["code"]
    client.post(
        "/workspaces/join",
        json={"code": code, "first_name": "Un", "last_name": "Rel"},
        headers=_account_headers(client, "unrelated@test.example"),
    )
    remaining = client.get(f"/workspaces/{ws}/invites", headers=headers).json()
    assert sorted(i["invite_type"] for i in remaining) == ["code", "email"]
