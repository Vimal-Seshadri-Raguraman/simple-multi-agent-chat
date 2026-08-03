"""POST /auth/discover: credential-based login discovery (SMAC-72 task 1, spec §2.5).

Binding security invariants under test:
- wrong-password and unknown-email produce byte-identical `{"workspaces": []}`
  responses (not just equal shape -- equal content, so neither can be used as
  an email/workspace enumeration oracle).
- every email-matching account is verified (no early exit once one matches).
- private workspaces DO appear for their own valid credentials.
- agent/bot accounts (no password_hash) are never discoverable.
- no tokens are issued by this endpoint.
"""

import app.database as database_module
from app.models import Member


def _found(client, workspace_name, email, password):
    """Found a new workspace + human founder account directly via POST /workspaces.

    Mirrors tests/test_unique_names.py's `_found` helper. Workspace names must
    be globally unique (SMAC-68), so callers vary `workspace_name` per call.
    """
    response = client.post(
        "/workspaces",
        json={
            "workspace_name": workspace_name,
            "visibility": "private",
            "email": email,
            "password": password,
            "first_name": "Test",
            "last_name": "User",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_discover_lists_all_my_workspaces(client):
    """Same email + password founds two workspaces (allowed: uniqueness is
    per-workspace, not global) -- discover must list both, ordered by name."""
    _found(client, "Alpha Co", "me@test.example", "pw-alpha-strong")
    _found(client, "Beta Co", "me@test.example", "pw-alpha-strong")
    response = client.post(
        "/auth/discover",
        json={"email": "me@test.example", "password": "pw-alpha-strong"},
    )
    assert response.status_code == 200
    names = [w["workspace_name"] for w in response.json()["workspaces"]]
    assert names == ["Alpha Co", "Beta Co"]


def test_discover_orders_by_workspace_name_not_creation_order(client):
    """Create Zeta before Alpha; discover must still come back alphabetical."""
    _found(client, "Zeta Co", "order@test.example", "pw-order-strong")
    _found(client, "Alpha Co", "order@test.example", "pw-order-strong")
    response = client.post(
        "/auth/discover",
        json={"email": "order@test.example", "password": "pw-order-strong"},
    )
    names = [w["workspace_name"] for w in response.json()["workspaces"]]
    assert names == ["Alpha Co", "Zeta Co"]


def test_discover_only_matching_password(client):
    """Same email, DIFFERENT passwords in two workspaces -> only the workspace
    whose account's password actually verifies is listed."""
    _found(client, "Gamma Co", "split@test.example", "pw-gamma-strong")
    _found(client, "Delta Co", "split@test.example", "pw-delta-strong")
    response = client.post(
        "/auth/discover",
        json={"email": "split@test.example", "password": "pw-gamma-strong"},
    )
    assert response.status_code == 200
    names = [w["workspace_name"] for w in response.json()["workspaces"]]
    assert names == ["Gamma Co"]


def test_wrong_password_unknown_email_identical(client):
    """Byte-identical bodies and status for: wrong password vs. unknown email."""
    _found(client, "Epsilon Co", "me2@test.example", "pw-epsilon-strong")
    r1 = client.post(
        "/auth/discover", json={"email": "me2@test.example", "password": "wrong"}
    )
    r2 = client.post(
        "/auth/discover", json={"email": "ghost@test.example", "password": "wrong"}
    )
    assert r1.status_code == r2.status_code == 200
    assert r1.content == r2.content == b'{"workspaces":[]}'


def test_no_accounts_at_all_matches_the_same_empty_body(client):
    """A genuinely-empty members table produces the identical empty body too."""
    response = client.post(
        "/auth/discover", json={"email": "nobody@test.example", "password": "whatever"}
    )
    assert response.status_code == 200
    assert response.content == b'{"workspaces":[]}'


def test_private_workspaces_do_appear_for_owner(client):
    """Private workspaces are the caller's own accounts -- they must appear."""
    found = _found(client, "Private Co", "priv@test.example", "pw-private-strong")
    assert found["workspace"]["visibility"] == "private"
    response = client.post(
        "/auth/discover",
        json={"email": "priv@test.example", "password": "pw-private-strong"},
    )
    workspaces = response.json()["workspaces"]
    assert len(workspaces) == 1
    assert workspaces[0]["workspace_id"] == found["workspace"]["workspace_id"]
    assert workspaces[0]["workspace_name"] == "Private Co"


def test_discover_issues_no_tokens(client):
    """The response body carries only workspace identifiers, never tokens."""
    _found(client, "NoToken Co", "notoken@test.example", "pw-notoken-strong")
    response = client.post(
        "/auth/discover",
        json={"email": "notoken@test.example", "password": "pw-notoken-strong"},
    )
    body = response.json()
    assert set(body.keys()) == {"workspaces"}
    for workspace in body["workspaces"]:
        assert set(workspace.keys()) == {"workspace_id", "workspace_name"}


def test_agents_never_discovered(client):
    """Agent/bot accounts have no password_hash. Per-workspace email
    uniqueness means an agent can't literally share a human's row, so this
    plants an agent with a matching email in a SECOND workspace that has no
    human account at all -- proving the human-with-a-hash filter (not just
    "an email column happens to be set") is what excludes it: the correct
    password for the real (human) account must list only that one
    workspace, never the agent-only one."""
    founder = _found(client, "Agents Co", "agentowner@test.example", "pw-agents-strong")
    agent_only = _found(
        client, "Agents Co Empty", "unused-founder@test.example", "irrelevant-pw"
    )
    with database_module.SessionLocal() as db:
        agent = Member(
            member_name="Bot Agent",
            member_type="agent",
            handle="bot-agent",
            email="agentowner@test.example",
            password_hash=None,
            workspace_id=agent_only["workspace"]["workspace_id"],
        )
        db.add(agent)
        db.commit()
    response = client.post(
        "/auth/discover",
        json={"email": "agentowner@test.example", "password": "pw-agents-strong"},
    )
    assert response.status_code == 200
    names = [w["workspace_name"] for w in response.json()["workspaces"]]
    assert names == ["Agents Co"]
    assert founder["workspace"]["workspace_name"] == "Agents Co"
