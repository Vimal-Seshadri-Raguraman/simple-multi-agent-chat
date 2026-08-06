"""SMAC-92: agent invite codes -- minting (capability-gated, `agent_code`
invite type) and unauthenticated redemption (`POST /agents/join`) that
returns the freshly minted agent's per-workspace API key directly."""

from datetime import timedelta

import app.database as database_module
from app import rate_limit as rate_limit_module
from app.models import WorkspaceInvite, utcnow
from tests.conftest import founder_auth, founder_headers, member_auth, member_headers


def _promote_to_agent_admin(client, ws: str, key: str) -> dict[str, str]:
    """Register a fresh human member and promote them to agent_admin via
    the real PATCH role endpoint (same pattern as test_privilege_matrix)."""
    headers = member_headers(client, key, "w1")
    member_id = member_auth(client, key, "w1")["member_id"]
    promote = client.patch(
        f"/workspaces/{ws}/members/{member_id}",
        json={"role": "agent_admin"},
        headers=founder_headers(client, "w1"),
    )
    assert promote.status_code == 200, promote.text
    return headers


def _mint_agent_code(client, headers: dict[str, str], workspace_id: str) -> str:
    r = client.post(
        f"/workspaces/{workspace_id}/invites",
        json={"invite_type": "agent_code"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["invite_type"] == "agent_code"
    code = r.json()["code"]
    assert isinstance(code, str) and len(code) >= 10
    return code


# --- Minting: capability-gated by Cap.MINT_AGENT_INVITES -------------------


def test_agent_admin_mints_agent_code(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    headers = _promote_to_agent_admin(client, ws, "aa")
    r = client.post(
        f"/workspaces/{ws}/invites", json={"invite_type": "agent_code"}, headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["invite_type"] == "agent_code"
    assert isinstance(body["code"], str) and len(body["code"]) >= 10
    assert body["expires_at"] is not None
    assert body["email"] is None


def test_agent_admin_cannot_mint_human_code(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    headers = _promote_to_agent_admin(client, ws, "aa")
    r = client.post(
        f"/workspaces/{ws}/invites", json={"invite_type": "code"}, headers=headers
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_plain_member_cannot_mint_agent_code(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    headers = member_headers(client, "plain", "w1")
    r = client.post(
        f"/workspaces/{ws}/invites", json={"invite_type": "agent_code"}, headers=headers
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_admin_can_mint_agent_code_too(client):
    """`admin` holds every capability, MINT_AGENT_INVITES included."""
    founder = founder_auth(client, "w1")
    code = _mint_agent_code(
        client, founder_headers(client, "w1"), founder["workspace_id"]
    )
    assert code


def test_agent_code_flows_through_list_and_revoke(client):
    """list/revoke already accept either mint cap (Task 2) -- verify
    agent_code rows actually flow through them, minted by agent_admin."""
    ws = founder_auth(client, "w1")["workspace_id"]
    headers = _promote_to_agent_admin(client, ws, "aa")
    invite = client.post(
        f"/workspaces/{ws}/invites", json={"invite_type": "agent_code"}, headers=headers
    ).json()

    listing = client.get(f"/workspaces/{ws}/invites", headers=headers).json()
    assert invite["invite_id"] in [i["invite_id"] for i in listing]

    revoke = client.delete(
        f"/workspaces/{ws}/invites/{invite['invite_id']}", headers=headers
    )
    assert revoke.status_code == 200
    assert invite["invite_id"] not in [
        i["invite_id"]
        for i in client.get(f"/workspaces/{ws}/invites", headers=headers).json()
    ]


# --- Redemption: POST /agents/join, unauthenticated ------------------------


def test_redeem_creates_agent_and_returns_key_once(client):
    founder = founder_auth(client, "w1")
    ws = founder["workspace_id"]
    code = _mint_agent_code(client, founder_headers(client, "w1"), ws)

    r = client.post("/agents/join", json={"code": code, "name": "Trader Bot"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["api_key"]
    assert body["account_id"]
    assert body["member_id"]
    assert body["handle"]
    assert body["workspace"]["workspace_id"] == ws

    # The key actually works and lands the agent inside this workspace.
    channels = client.get(
        f"/workspaces/{ws}/channels", headers={"X-API-Key": body["api_key"]}
    )
    assert channels.status_code == 200

    profile = client.get("/members/me", headers={"X-API-Key": body["api_key"]}).json()
    assert profile["member_id"] == body["member_id"]
    assert profile["workspace_id"] == ws

    # The code is burnt: an identical retry gets the uniform invalid-invite
    # 404, indistinguishable from a bogus code.
    r2 = client.post("/agents/join", json={"code": code, "name": "Copy Cat"})
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "invalid_invite"
    assert r2.json()["error"]["message"] == "Invite is invalid or expired"


def test_redeemed_agent_carries_ordinary_agent_caps_only(client):
    """Redemption is not a backdoor to elevated capabilities: the minted
    key carries the standard agent type-cap intersection (post/read/
    ack_mentions), same as any other agent -- it cannot manage members."""
    founder = founder_auth(client, "w1")
    ws = founder["workspace_id"]
    code = _mint_agent_code(client, founder_headers(client, "w1"), ws)
    body = client.post("/agents/join", json={"code": code, "name": "Trader Bot"}).json()

    r = client.post(
        "/members/agents",
        json={"member_name": "Another"},
        headers={"X-API-Key": body["api_key"]},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_bogus_code_rejected(client):
    r = client.post("/agents/join", json={"code": "not-a-real-code", "name": "Ghost"})
    assert r.status_code == 404
    assert r.json() == {
        "error": {"code": "invalid_invite", "message": "Invite is invalid or expired"}
    }


def test_revoked_agent_code_rejected(client):
    founder = founder_auth(client, "w1")
    ws = founder["workspace_id"]
    headers = founder_headers(client, "w1")
    invite = client.post(
        f"/workspaces/{ws}/invites", json={"invite_type": "agent_code"}, headers=headers
    ).json()
    client.delete(f"/workspaces/{ws}/invites/{invite['invite_id']}", headers=headers)

    r = client.post("/agents/join", json={"code": invite["code"], "name": "Late Bot"})
    assert r.status_code == 404
    assert r.json() == {
        "error": {"code": "invalid_invite", "message": "Invite is invalid or expired"}
    }


def test_expired_agent_code_rejected_and_deleted(client):
    founder = founder_auth(client, "w1")
    ws = founder["workspace_id"]
    code = _mint_agent_code(client, founder_headers(client, "w1"), ws)

    with database_module.SessionLocal() as db:
        row = db.query(WorkspaceInvite).filter(WorkspaceInvite.code == code).first()
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.add(row)
        db.commit()
        invite_id = row.invite_id

    r = client.post("/agents/join", json={"code": code, "name": "Late Bot"})
    assert r.status_code == 404
    assert r.json() == {
        "error": {"code": "invalid_invite", "message": "Invite is invalid or expired"}
    }
    with database_module.SessionLocal() as db:
        assert db.get(WorkspaceInvite, invite_id) is None  # expired-on-sight cleanup


def test_expired_revoked_and_bogus_are_byte_identical(client):
    """All three distinct failure modes must produce the exact same body
    -- a caller can never learn which one happened."""
    founder = founder_auth(client, "w1")
    ws = founder["workspace_id"]
    headers = founder_headers(client, "w1")

    bogus = client.post("/agents/join", json={"code": "totally-bogus", "name": "X"})

    revoked_invite = client.post(
        f"/workspaces/{ws}/invites", json={"invite_type": "agent_code"}, headers=headers
    ).json()
    client.delete(
        f"/workspaces/{ws}/invites/{revoked_invite['invite_id']}", headers=headers
    )
    revoked = client.post(
        "/agents/join", json={"code": revoked_invite["code"], "name": "X"}
    )

    expired_code = _mint_agent_code(client, headers, ws)
    with database_module.SessionLocal() as db:
        row = (
            db.query(WorkspaceInvite)
            .filter(WorkspaceInvite.code == expired_code)
            .first()
        )
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.add(row)
        db.commit()
    expired = client.post("/agents/join", json={"code": expired_code, "name": "X"})

    for response in (bogus, revoked, expired):
        assert response.status_code == 404
        assert response.json() == {
            "error": {
                "code": "invalid_invite",
                "message": "Invite is invalid or expired",
            }
        }


def test_human_code_cannot_be_redeemed_as_agent(client):
    founder = founder_auth(client, "w1")
    ws = founder["workspace_id"]
    human_invite = client.post(
        f"/workspaces/{ws}/invites",
        json={"invite_type": "code"},
        headers=founder_headers(client, "w1"),
    ).json()

    r = client.post(
        "/agents/join", json={"code": human_invite["code"], "name": "Impersonator"}
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "invalid_invite"

    # And the human code survives untouched -- the failed agent-join
    # attempt didn't burn it.
    still_there = client.get(
        f"/workspaces/{ws}/invites", headers=founder_headers(client, "w1")
    ).json()
    assert human_invite["invite_id"] in [i["invite_id"] for i in still_there]


def test_concurrent_redemption_only_one_claim_succeeds(client):
    """Simulates two concurrent redemptions of the SAME code racing past
    the lookup/expiry/workspace checks before either has committed its
    claim. Two independent DB sessions stand in for two concurrent
    request handlers -- SQLite has exactly one writer at a time
    regardless of thread count, so any truly concurrent attempt
    resolves to exactly this ordering at the DB level (see the task
    report for the full analysis of why a real multi-threaded test
    against this fixture's shared StaticPool connection wouldn't prove
    anything stronger). Exercises the identical query shape
    `join_as_agent` uses: a bulk DELETE keyed by invite_id, whose
    rowcount is the single source of truth for who won the race."""
    founder = founder_auth(client, "w1")
    ws = founder["workspace_id"]
    code = _mint_agent_code(client, founder_headers(client, "w1"), ws)

    with database_module.SessionLocal() as db_a, database_module.SessionLocal() as db_b:
        invite_a = (
            db_a.query(WorkspaceInvite)
            .filter(
                WorkspaceInvite.code == code,
                WorkspaceInvite.invite_type == "agent_code",
            )
            .first()
        )
        invite_b = (
            db_b.query(WorkspaceInvite)
            .filter(
                WorkspaceInvite.code == code,
                WorkspaceInvite.invite_type == "agent_code",
            )
            .first()
        )
        # Both "requests" observed the code as valid before either claimed it.
        assert invite_a is not None and invite_b is not None

        claimed_a = (
            db_a.query(WorkspaceInvite)
            .filter(WorkspaceInvite.invite_id == invite_a.invite_id)
            .delete(synchronize_session=False)
        )
        db_a.commit()
        claimed_b = (
            db_b.query(WorkspaceInvite)
            .filter(WorkspaceInvite.invite_id == invite_b.invite_id)
            .delete(synchronize_session=False)
        )
        db_b.commit()

    assert (claimed_a, claimed_b) == (1, 0)  # exactly one winner, never both/neither


def test_redeem_is_rate_limited(client, monkeypatch):
    small_limiter = rate_limit_module.SlidingWindowRateLimiter(
        max_events=3, window_seconds=60
    )
    monkeypatch.setattr(rate_limit_module, "agent_join_limiter", small_limiter)

    for _ in range(3):
        r = client.post("/agents/join", json={"code": "bogus", "name": "X"})
        assert r.status_code == 404  # budget consumed, but not yet exhausted

    r = client.post("/agents/join", json={"code": "bogus", "name": "X"})
    assert r.status_code == 429
    assert r.json() == {
        "error": {
            "code": "rate_limited",
            "message": "Too many attempts -- wait a moment",
        }
    }


def test_agent_join_rate_limit_is_not_bypassed_by_a_real_code(client, monkeypatch):
    """The limiter runs before the code is even looked up, so it caps a
    real-code retry storm too, not just bogus-code brute-forcing."""
    small_limiter = rate_limit_module.SlidingWindowRateLimiter(
        max_events=1, window_seconds=60
    )
    monkeypatch.setattr(rate_limit_module, "agent_join_limiter", small_limiter)

    founder = founder_auth(client, "w1")
    ws = founder["workspace_id"]
    code = _mint_agent_code(client, founder_headers(client, "w1"), ws)

    first = client.post("/agents/join", json={"code": code, "name": "First"})
    assert first.status_code == 201

    second = client.post("/agents/join", json={"code": code, "name": "Second"})
    assert second.status_code == 429
