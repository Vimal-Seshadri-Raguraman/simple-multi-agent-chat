"""Identity v2 multi-workspace journeys (SMAC-79 Task 4, spec §0): the
mental-model diagram, realized as a test.

Two stories, one account each:

1. **Bob** (human): one global account, two workspace BADGES -- founds
   "AI Finance Co" (admin there) and joins "Hedge House" via a shareable
   code (NOT admin there). Different display names, different @handles,
   independent unread/mention state per workspace, and the wall: a
   workspace-A token 404s against workspace B's endpoints.
2. **The "Analyst" agent**: one global agent account attached to both
   workspaces, minting a separate API key per workspace -- each key sees
   ONLY its own workspace's mention inbox, and handles dedupe locally
   and independently per workspace (spec Decision 2's worked example).
"""

from __future__ import annotations

from typing import Any

from tests.conftest import _create_account, founder_auth, founder_headers

_PASSWORD = "test-password-123"  # matches conftest._TEST_PASSWORD


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _account_id_of(
    client: Any, workspace_id: str, member_id: str, headers: dict[str, str]
) -> str:
    """Look up a member's `account_id` via the real, workspace-scoped
    `GET /workspaces/{id}/members` listing (no direct DB access -- the
    journey stays entirely over the wire)."""
    members = client.get(f"/workspaces/{workspace_id}/members", headers=headers).json()
    return next(m["account_id"] for m in members if m["member_id"] == member_id)


def test_bob_one_account_two_workspace_badges_diagram(client: Any) -> None:
    """Spec §0's left diagram, end to end: Bob's ONE account holds two
    independent per-workspace profiles (badges) -- different names,
    different handles, admin in exactly one, and fully isolated unread +
    mention state, with the workspace wall enforced both ways."""
    # --- Bob's global account ------------------------------------------------
    bob = _create_account(client, "bob@example.com")
    bob_account_token = bob["tokens"]["access_token"]

    # --- Bob FOUNDS "AI Finance Co" -- admin there ----------------------------
    founded = client.post(
        "/workspaces",
        json={
            "workspace_name": "AI Finance Co",
            "visibility": "public",
            "display_first_name": "Finance",
            "display_last_name": "Analyst",
        },
        headers=_bearer(bob_account_token),
    )
    assert founded.status_code == 200, founded.text
    founded = founded.json()
    workspace_a = founded["workspace"]["workspace_id"]
    bob_token_a = founded["access_token"]
    bob_member_a = founded["member"]
    assert bob_member_a["role"] == "admin"
    assert "is_admin" not in bob_member_a  # removed SMAC-92 Task 4 (web/TUI migrated)
    assert bob_member_a["member_name"] == "Finance Analyst"
    handle_a = bob_member_a["handle"]

    # --- A second workspace, "Hedge House", founded by someone else ----------
    hedge_founder = _create_account(client, "hedge-founder@example.com")
    hedge_founded = client.post(
        "/workspaces",
        json={
            "workspace_name": "Hedge House",
            "visibility": "private",
            "display_first_name": "Hedge",
            "display_last_name": "Founder",
        },
        headers=_bearer(hedge_founder["tokens"]["access_token"]),
    ).json()
    workspace_b = hedge_founded["workspace"]["workspace_id"]
    hedge_founder_token = hedge_founded["access_token"]

    # A shareable code, minted by Hedge House's own admin.
    invite = client.post(
        f"/workspaces/{workspace_b}/invites",
        json={"invite_type": "code"},
        headers=_bearer(hedge_founder_token),
    )
    assert invite.status_code == 200, invite.text
    code = invite.json()["code"]

    # --- Bob JOINS "Hedge House" via the code -- a DIFFERENT display name,
    #     a DIFFERENT handle, and NOT admin there -----------------------------
    joined = client.post(
        "/workspaces/join",
        json={
            "code": code,
            "first_name": "Trader",
            "last_name": "Bob",
        },
        headers=_bearer(bob_account_token),
    )
    assert joined.status_code == 200, joined.text
    joined = joined.json()
    assert joined["workspace"]["workspace_id"] == workspace_b
    bob_token_b = joined["access_token"]
    bob_member_b = joined["member"]
    assert bob_member_b["role"] == "member"
    assert "is_admin" not in bob_member_b  # removed SMAC-92 Task 4 (web/TUI migrated)
    assert bob_member_b["member_name"] == "Trader Bob"
    handle_b = bob_member_b["handle"]

    # --- Same account, different badges: different names, different handles --
    assert handle_a != handle_b
    assert (
        bob_member_a["account_id"]
        == bob_member_b["account_id"]
        == bob["account"]["account_id"]
    )

    # --- Independent mention inboxes: mention Bob in EACH workspace ----------
    # AI Finance Co: Bob is the only human there, so add a colleague (public
    # workspace, so a direct self-service registration works) to do the
    # mentioning.
    colleague = _create_account(client, "colleague@example.com")
    colleague_reg = client.post(
        f"/workspaces/{workspace_a}/register",
        json={"first_name": "Cole", "last_name": "League"},
        headers=_bearer(colleague["tokens"]["access_token"]),
    ).json()
    colleague_token_a = colleague_reg["access_token"]

    general_a = [
        c["channel_id"]
        for c in client.get(
            f"/workspaces/{workspace_a}/channels", headers=_bearer(bob_token_a)
        ).json()
        if c["channel_name"] == "general"
    ][0]
    posted_a = client.post(
        f"/workspaces/{workspace_a}/channels/{general_a}/messages",
        json={"message_text": f"@{handle_a} welcome aboard"},
        headers=_bearer(colleague_token_a),
    )
    assert posted_a.status_code == 200, posted_a.text

    general_b = [
        c["channel_id"]
        for c in client.get(
            f"/workspaces/{workspace_b}/channels", headers=_bearer(bob_token_b)
        ).json()
        if c["channel_name"] == "general"
    ][0]
    posted_b = client.post(
        f"/workspaces/{workspace_b}/channels/{general_b}/messages",
        json={"message_text": f"@{handle_b} your desk is ready"},
        headers=_bearer(hedge_founder_token),
    )
    assert posted_b.status_code == 200, posted_b.text

    # --- Each workspace's mention inbox holds ONLY that workspace's mention --
    inbox_a = client.get("/mentions", headers=_bearer(bob_token_a)).json()
    inbox_b = client.get("/mentions", headers=_bearer(bob_token_b)).json()
    assert len(inbox_a) == 1
    assert "welcome aboard" in inbox_a[0]["message"]["Message"]["message_text"]
    assert inbox_a[0]["message"]["workspace"]["workspace_id"] == workspace_a
    assert len(inbox_b) == 1
    assert "your desk is ready" in inbox_b[0]["message"]["Message"]["message_text"]
    assert inbox_b[0]["message"]["workspace"]["workspace_id"] == workspace_b

    # --- Independent unreads: workspace A shows Bob's mention, workspace B's
    #     unread state is untouched by it, and vice versa ---------------------
    unreads_a = client.get(
        f"/workspaces/{workspace_a}/unreads", headers=_bearer(bob_token_a)
    ).json()["unreads"]
    general_row_a = next(r for r in unreads_a if r["channel_id"] == general_a)
    assert general_row_a["mention_count"] == 1

    unreads_b = client.get(
        f"/workspaces/{workspace_b}/unreads", headers=_bearer(bob_token_b)
    ).json()["unreads"]
    general_row_b = next(r for r in unreads_b if r["channel_id"] == general_b)
    assert general_row_b["mention_count"] == 1

    # Acking in A doesn't touch B's mention count.
    client.post(
        f"/mentions/{inbox_a[0]['mention_id']}/ack", headers=_bearer(bob_token_a)
    )
    unreads_a_after = client.get(
        f"/workspaces/{workspace_a}/unreads", headers=_bearer(bob_token_a)
    ).json()["unreads"]
    assert (
        next(r for r in unreads_a_after if r["channel_id"] == general_a)[
            "mention_count"
        ]
        == 0
    )
    unreads_b_after = client.get(
        f"/workspaces/{workspace_b}/unreads", headers=_bearer(bob_token_b)
    ).json()["unreads"]
    assert (
        next(r for r in unreads_b_after if r["channel_id"] == general_b)[
            "mention_count"
        ]
        == 1
    )

    # --- The wall: workspace-A's token 404s against workspace B's endpoints --
    wall_members = client.get(
        f"/workspaces/{workspace_b}/members", headers=_bearer(bob_token_a)
    )
    assert wall_members.status_code == 404
    wall_unreads = client.get(
        f"/workspaces/{workspace_b}/unreads", headers=_bearer(bob_token_a)
    )
    assert wall_unreads.status_code == 404
    wall_post = client.post(
        f"/workspaces/{workspace_b}/channels/{general_b}/messages",
        json={"message_text": "should never land"},
        headers=_bearer(bob_token_a),
    )
    assert wall_post.status_code == 404
    # And the reverse direction, for good measure.
    wall_reverse = client.get(
        f"/workspaces/{workspace_a}/members", headers=_bearer(bob_token_b)
    )
    assert wall_reverse.status_code == 404


def test_agent_one_account_two_workspace_keys_diagram(client: Any) -> None:
    """Spec §0's right diagram: one agent ACCOUNT attached to two
    workspaces mints two independent API keys; each key sees ONLY its own
    workspace's mention inbox, and the handle dedupes locally and
    independently per workspace (Decision 2's @analyst / @analyst2
    worked example)."""
    workspace_a = founder_auth(client, "agent-diagram-a")["workspace_id"]
    workspace_b = founder_auth(client, "agent-diagram-b")["workspace_id"]
    headers_a = founder_headers(client, "agent-diagram-a")
    headers_b = founder_headers(client, "agent-diagram-b")

    # The agent is born in workspace A.
    key_a = client.post(
        "/members/agents",
        json={"member_name": "Analyst"},
        headers=headers_a,
    ).json()
    assert key_a["handle"] == "analyst"
    account_id = _account_id_of(client, workspace_a, key_a["member_id"], headers_a)

    # Workspace B already has an UNRELATED "analyst" handle in use, so the
    # attach below must dedupe locally to "analyst2" -- independent of
    # workspace A, which still says plain "analyst".
    collider = client.post(
        "/members/agents",
        json={"member_name": "Analyst"},
        headers=headers_b,
    ).json()
    assert collider["handle"] == "analyst"

    # Attach the SAME agent account into workspace B: a second, independent
    # membership + a brand-new key.
    key_b = client.post(
        "/members/agents",
        json={"account_id": account_id},
        headers=headers_b,
    )
    assert key_b.status_code == 200, key_b.text
    key_b = key_b.json()
    assert key_b["handle"] == "analyst2"
    assert key_b["api_key"] != key_a["api_key"]

    # Mention the agent in EACH workspace, over its own key's channel.
    general_a = [
        c["channel_id"]
        for c in client.get(
            f"/workspaces/{workspace_a}/channels", headers=headers_a
        ).json()
        if c["channel_name"] == "general"
    ][0]
    general_b = [
        c["channel_id"]
        for c in client.get(
            f"/workspaces/{workspace_b}/channels", headers=headers_b
        ).json()
        if c["channel_name"] == "general"
    ][0]
    client.post(
        f"/workspaces/{workspace_a}/channels/{general_a}/messages",
        json={"message_text": f"@{key_a['handle']} run the morning numbers"},
        headers=headers_a,
    )
    client.post(
        f"/workspaces/{workspace_b}/channels/{general_b}/messages",
        json={"message_text": f"@{key_b['handle']} check the hedge book"},
        headers=headers_b,
    )

    # --- Each key's inbox holds ONLY its own workspace's mention -------------
    inbox_a = client.get("/mentions", headers={"X-API-Key": key_a["api_key"]}).json()
    inbox_b = client.get("/mentions", headers={"X-API-Key": key_b["api_key"]}).json()
    assert len(inbox_a) == 1
    assert "morning numbers" in inbox_a[0]["message"]["Message"]["message_text"]
    assert inbox_a[0]["message"]["workspace"]["workspace_id"] == workspace_a
    assert len(inbox_b) == 1
    assert "hedge book" in inbox_b[0]["message"]["Message"]["message_text"]
    assert inbox_b[0]["message"]["workspace"]["workspace_id"] == workspace_b

    # Each key is also blind to the OTHER workspace's membership listing --
    # X-API-Key auth resolves the member directly, the same wall applies.
    cross = client.get(
        f"/workspaces/{workspace_b}/members", headers={"X-API-Key": key_a["api_key"]}
    )
    assert cross.status_code == 404
