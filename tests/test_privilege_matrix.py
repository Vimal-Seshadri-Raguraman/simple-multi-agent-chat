"""SMAC-92: the capability-gate privilege matrix across every route this
task gated, plus the forbidden-envelope shape and the agent-key-cannot-
manage floor (type cap always wins, regardless of role)."""

import pytest

from tests.conftest import founder_auth, founder_headers, member_auth, member_headers

# (actor, method, path_template, body, expected_status)
CASES = [
    ("member", "POST", "/workspaces/{ws}/invites", {"invite_type": "code"}, 403),
    ("agent_admin", "POST", "/workspaces/{ws}/invites", {"invite_type": "code"}, 403),
    ("admin", "POST", "/workspaces/{ws}/invites", {"invite_type": "code"}, 200),
    ("member", "POST", "/members/agents", {"member_name": "Bot"}, 403),
    ("agent_admin", "POST", "/members/agents", {"member_name": "Bot"}, 200),
    ("member", "PATCH", "/workspaces/{ws}", {"visibility": "private"}, 403),
    ("agent_admin", "PATCH", "/workspaces/{ws}", {"visibility": "private"}, 403),
    ("member", "GET", "/workspaces/{ws}/export", None, 403),
    ("member", "PATCH", "/workspaces/{ws}/members/{other}", {"role": "admin"}, 403),
    ("member", "DELETE", "/workspaces/{ws}/members/{other}", None, 403),
    ("admin", "DELETE", "/workspaces/{ws}/members/{other}", None, 200),
]


def _actor_headers(client, ws: str, actor: str) -> dict[str, str]:
    """Bearer headers for one of the matrix's three human roles. `admin` is
    the workspace founder; `member`/`agent_admin` are registered via the
    real join door and (for agent_admin) promoted via the real PATCH role
    endpoint -- role changes are resolved live per request (Task 1), so
    the already-issued token reflects the promotion on its very next call."""
    if actor == "admin":
        return founder_headers(client, "w1")
    headers = member_headers(client, actor, "w1")
    if actor == "agent_admin":
        member_id = member_auth(client, actor, "w1")["member_id"]
        promote = client.patch(
            f"/workspaces/{ws}/members/{member_id}",
            json={"role": "agent_admin"},
            headers=founder_headers(client, "w1"),
        )
        assert promote.status_code == 200, promote.text
    return headers


@pytest.mark.parametrize("actor,method,path_template,body,expected_status", CASES)
def test_privilege_matrix(client, actor, method, path_template, body, expected_status):
    ws = founder_auth(client, "w1")["workspace_id"]
    other = member_auth(client, "other", "w1")["member_id"]
    headers = _actor_headers(client, ws, actor)
    path = path_template.format(ws=ws, other=other)
    response = client.request(method, path, json=body, headers=headers)
    assert response.status_code == expected_status, response.text


def test_forbidden_envelope_shape(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    headers = member_headers(client, "plain", "w1")
    r = client.post(
        f"/workspaces/{ws}/invites", json={"invite_type": "code"}, headers=headers
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"
    assert "requires mint_human_invites" in r.json()["error"]["message"]


def test_agent_key_cannot_manage(client):
    """Type cap: an agent credential can never hit management routes,
    regardless of the underlying member's role (agent_admin included --
    the type-cap intersection is applied LAST, per app/capabilities.py)."""
    founder = founder_auth(client, "w1")
    agent = client.post(
        "/members/agents",
        json={"member_name": "Ev"},
        headers=founder_headers(client, "w1"),
    ).json()
    r = client.post(
        "/members/agents",
        json={"member_name": "Vi"},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


# --- GET /workspaces/{id}/members: VIEW_MEMBERS gate decision -------------
#
# Task 1's reviewer flagged this route as ungated. Investigated for this
# task: every human role holds Cap.VIEW_MEMBERS (app/capabilities.py's
# _MEMBER_CAPS), so gating only bites the type-cap path (an agent/bot_app
# key, whose caps intersect down to {post, read, ack_mentions}). Checked
# every agent-key call site in smac_mcp/ (the MCP bridge): it only ever
# calls /members/me, /workspaces/{id}/{channels,unreads}, channel
# messages/read -- never this listing route -- so gating it cannot
# regress the bridge. Gated it below; these two tests are that decision's
# regression coverage.


def test_agent_key_cannot_list_members(client):
    founder = founder_auth(client, "w1")
    agent = client.post(
        "/members/agents",
        json={"member_name": "Ev"},
        headers=founder_headers(client, "w1"),
    ).json()
    r = client.get(
        f"/workspaces/{founder['workspace_id']}/members",
        headers={"X-API-Key": agent["api_key"]},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_every_human_role_can_list_members(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    for actor in ("member", "agent_admin", "admin"):
        headers = _actor_headers(client, ws, actor)
        r = client.get(f"/workspaces/{ws}/members", headers=headers)
        assert r.status_code == 200, (actor, r.text)
