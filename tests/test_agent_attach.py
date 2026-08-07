"""Agent/bot account-attach flow (spec §4, SMAC-79 Task 2): `POST
/members/agents` (and `/members/bots`) with `{account_id}` attaches an
EXISTING agent/bot_app account as a new per-workspace membership --
same identity, a fresh per-workspace key, handle deduped locally.

Also covers the spec §1 invariant required by Task 1's review: every
member's `member_type` equals its linked account's `account_type`.
"""

from datetime import datetime, timezone

import app.database as database_module
from app.models import Account, Member
from tests.conftest import founder_auth, founder_headers, member_auth


def test_same_agent_account_two_workspaces_two_keys(client):
    """Creating an agent in workspace A, then attaching that same account
    to workspace B, mints a SECOND, DIFFERENT key -- one identity, one
    membership (and one key) per workspace (spec Decision 2)."""
    founder_auth(client, "wa")
    founder_auth(client, "wb")

    created = client.post(
        "/members/agents",
        json={"member_name": "Analyst"},
        headers=founder_headers(client, "wa"),
    )
    assert created.status_code == 200
    created_body = created.json()
    account_id = None
    with database_module.SessionLocal() as db:
        member = (
            db.query(Member).filter(Member.member_id == created_body["member_id"]).one()
        )
        account_id = member.account_id

    attached = client.post(
        "/members/agents",
        json={"account_id": account_id},
        headers=founder_headers(client, "wb"),
    )
    assert attached.status_code == 200
    attached_body = attached.json()

    assert attached_body["member_id"] != created_body["member_id"]
    assert attached_body["api_key"] != created_body["api_key"]
    assert attached_body["member_type"] == "agent"

    # Each key only works in its own workspace -- re-expressed via
    # /members/me (final review F2): /members is now gated on
    # Cap.VIEW_MEMBERS, which an agent key doesn't hold, so it can no
    # longer serve as this isolation probe. /members/me reports the
    # caller's own member_id and workspace_id under either key, which is
    # exactly what cross-workspace isolation needs to assert.
    a_only = client.get("/members/me", headers={"X-API-Key": created_body["api_key"]})
    assert a_only.status_code == 200
    b_only = client.get("/members/me", headers={"X-API-Key": attached_body["api_key"]})
    assert b_only.status_code == 200
    a_self = a_only.json()
    b_self = b_only.json()
    assert a_self["member_id"] == created_body["member_id"]
    assert b_self["member_id"] == attached_body["member_id"]
    assert a_self["workspace_id"] != b_self["workspace_id"]


def test_attach_dedupes_handle_locally(client):
    """Decision 2's worked example: attaching @analyst into a workspace
    that already has an "analyst" handle dedupes to @analyst2, exactly
    like any other handle collision -- purely local to that workspace."""
    founder_auth(client, "wa")
    founder_auth(client, "wb")

    created = client.post(
        "/members/agents",
        json={"member_name": "Analyst"},
        headers=founder_headers(client, "wa"),
    ).json()
    assert created["handle"] == "analyst"
    with database_module.SessionLocal() as db:
        member = db.query(Member).filter(Member.member_id == created["member_id"]).one()
        account_id = member.account_id

    # A pre-existing, unrelated "analyst" handle already claims the slot
    # in workspace B.
    collider = client.post(
        "/members/agents",
        json={"member_name": "Analyst"},
        headers=founder_headers(client, "wb"),
    ).json()
    assert collider["handle"] == "analyst"

    attached = client.post(
        "/members/agents",
        json={"account_id": account_id},
        headers=founder_headers(client, "wb"),
    )
    assert attached.status_code == 200
    assert attached.json()["handle"] == "analyst2"
    assert attached.json()["member_name"] == "Analyst"


def test_attach_name_source_is_deterministic_oldest_membership(client):
    """Final-review MINOR-3: `_attach`'s source membership must be the
    account's OLDEST (by created_at, then member_id) -- not an arbitrary,
    unordered row. Back-dates the SECOND-inserted membership to be older
    (by `created_at`) than the first-inserted one and gives it a
    distinguishable name: a third attach must copy the back-dated row's
    name, proving the source is picked by `created_at`/`member_id`
    ordering and not by insertion (rowid) order, which an unordered
    `.first()` would have returned instead.
    """
    founder_auth(client, "wa")
    founder_auth(client, "wb")
    founder_auth(client, "wc")

    first = client.post(
        "/members/agents",
        json={"member_name": "First Created"},
        headers=founder_headers(client, "wa"),
    ).json()
    with database_module.SessionLocal() as db:
        member = db.query(Member).filter(Member.member_id == first["member_id"]).one()
        account_id = member.account_id

    second = client.post(
        "/members/agents",
        json={"account_id": account_id},
        headers=founder_headers(client, "wb"),
    ).json()

    with database_module.SessionLocal() as db:
        row = db.query(Member).filter(Member.member_id == second["member_id"]).one()
        row.member_name = "Actually Oldest"
        row.created_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        db.add(row)
        db.commit()

    third = client.post(
        "/members/agents",
        json={"account_id": account_id},
        headers=founder_headers(client, "wc"),
    ).json()
    assert third["member_name"] == "Actually Oldest"


def test_attach_already_a_member_is_409(client):
    founder_auth(client, "wa")
    created = client.post(
        "/members/agents",
        json={"member_name": "Bot"},
        headers=founder_headers(client, "wa"),
    ).json()
    with database_module.SessionLocal() as db:
        member = db.query(Member).filter(Member.member_id == created["member_id"]).one()
        account_id = member.account_id

    replay = client.post(
        "/members/agents",
        json={"account_id": account_id},
        headers=founder_headers(client, "wa"),
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "already_a_member"


def test_attach_unknown_account_id_is_404(client):
    founder_auth(client, "wa")
    response = client.post(
        "/members/agents",
        json={"account_id": "does-not-exist"},
        headers=founder_headers(client, "wa"),
    )
    assert response.status_code == 404


def test_attach_wrong_account_type_is_404(client):
    """A bot_app account can't be attached via /members/agents (or vice
    versa) -- indistinguishable from not-found, same "wall" pattern as
    every other cross-entity reference."""
    founder_auth(client, "wa")
    founder_auth(client, "wb")
    bot = client.post(
        "/members/bots",
        json={"member_name": "Zapier"},
        headers=founder_headers(client, "wa"),
    ).json()
    with database_module.SessionLocal() as db:
        member = db.query(Member).filter(Member.member_id == bot["member_id"]).one()
        account_id = member.account_id

    response = client.post(
        "/members/agents",
        json={"account_id": account_id},
        headers=founder_headers(client, "wb"),
    )
    assert response.status_code == 404


def test_body_requires_exactly_one_of_member_name_or_account_id(client):
    founder_auth(client, "wa")
    headers = founder_headers(client, "wa")
    neither = client.post("/members/agents", json={}, headers=headers)
    assert neither.status_code == 422
    both = client.post(
        "/members/agents",
        json={"member_name": "X", "account_id": "y"},
        headers=headers,
    )
    assert both.status_code == 422


def test_member_type_equals_linked_account_type_invariant(client):
    """Spec §1's binding invariant (flagged by Task 1's review): every
    member's member_type must equal its linked account's account_type --
    for humans (founder/registered/joined), agents, and bots alike."""
    founder = founder_auth(client, "wa")
    member_auth(client, "m2", "wa")
    client.post(
        "/members/agents",
        json={"member_name": "Agent One"},
        headers=founder_headers(client, "wa"),
    )
    client.post(
        "/members/bots",
        json={"member_name": "Bot One"},
        headers=founder_headers(client, "wa"),
    )

    with database_module.SessionLocal() as db:
        members = (
            db.query(Member)
            .filter(Member.workspace_id == founder["workspace_id"])
            .all()
        )
        assert len(members) == 4  # founder, m2, agent, bot
        for member in members:
            account = db.get(Account, member.account_id)
            assert account is not None
            assert member.member_type == account.account_type, (
                f"member '{member.member_id}' has member_type"
                f" '{member.member_type}' but its account has account_type"
                f" '{account.account_type}'"
            )
