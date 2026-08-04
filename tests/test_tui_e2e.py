"""The end-to-end human journey (SMAC-72 task 6, final; adapted for
Identity v2 in SMAC-79 Task 3): spec §0.1's storyline, start to finish,
against a REAL spawned `smac-server` and the real `SmacApp` driven
through Textual's own `App.run_test()`/`Pilot` -- no `FakeApi` anywhere
in this module.

/register (account-only) -> lands in "no workspace yet" -> /workspace
create founds the workspace, lands in #general (Frame 4) -> bare-text
send (Frame 5) -> a second identity (an agent, minted via `POST
/members/agents` -- the TUI itself has no agent-auth) replies mentioning
the human, live -> a mention in ANOTHER channel rings the bell (Frame 6)
-> `/channel <name>` switches (Frame 7) -> `/channels` shows everything
caught up -> `/quit` (session kept) -> a BRAND NEW `SmacApp`/`SmacApi`
pair, same `$HOME`, lands straight into #general with no login screen at
all (Frame 8) -- "every day after starts at Frame 8" (spec §0.1's
closing line).

Deliberately one long test rather than many small ones: the whole point
of an e2e test is that each step's state (the session, the channel
memberships, the read cursors) is exactly what the PREVIOUS step left
behind, not a fixture's clean-room setup.

A second module-level journey (SMAC-79 Task 4, spec §0's closing story)
adds the piece the single-user test above can't reach: Alice and Bob on
TWO SEPARATE `$HOME`s, sharing one real server -- `/register` -> `/workspace
create` -> `/invite` on Alice's side, `/register` -> `/join <code>` on
Bob's, a live cross-account @mention, and Bob's own Frame-8 relaunch on
his own `$HOME`, independent of Alice's session entirely.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from smac_cli.api import Session, SmacApi
from smac_cli.app import SmacApp

_TEST_PASSWORD = "test-password-123"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _wait_until(pilot: Any, predicate: Any, *, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout}s")
        await pilot.pause(0.02)


async def _post_until_seen(
    pilot: Any,
    post_fn: Any,
    contains: str,
    app: SmacApp,
    *,
    attempts: int = 6,
    per_attempt_timeout: float = 1.0,
) -> None:
    """Re-post until `contains` shows up in the feed -- a message posted
    the instant a channel's live feed/mention-bell thread starts can beat
    that thread's own WebSocket handshake (see `test_tui_live.py`'s copy
    of this helper for the full explanation); re-posting is harmless for
    an "eventually shows up" assertion.
    """
    for _ in range(attempts):
        post_fn()
        deadline = time.monotonic() + per_attempt_timeout
        while time.monotonic() < deadline:
            if any(contains in line for line in app._log_lines):
                return
            await pilot.pause(0.02)
    raise AssertionError(f"{contains!r} never appeared after {attempts} attempts")


def _body_text(app: SmacApp) -> str:
    return "\n".join(app._log_lines)


def _register_agent(
    url: str, workspace_id: str, bearer: str, name: str
) -> dict[str, Any]:
    resp = httpx.post(
        f"{url}/members/agents",
        json={"member_name": name},
        headers={"Authorization": f"Bearer {bearer}"},
        timeout=10.0,
    )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return data


def _add_channel_member(
    url: str, workspace_id: str, bearer: str, channel_id: str, member_id: str
) -> None:
    resp = httpx.post(
        f"{url}/workspaces/{workspace_id}/channels/{channel_id}/members",
        json={"member_id": member_id},
        headers={"Authorization": f"Bearer {bearer}"},
        timeout=10.0,
    )
    resp.raise_for_status()


def _agent_post(
    url: str, workspace_id: str, channel_id: str, api_key: str, text: str
) -> dict[str, Any]:
    resp = httpx.post(
        f"{url}/workspaces/{workspace_id}/channels/{channel_id}/messages",
        json={"message_text": text},
        headers={"X-API-Key": api_key},
        timeout=10.0,
    )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return data


@pytest.mark.anyio
async def test_the_full_human_journey_register_to_relaunch(
    real_smac_server: tuple[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url, home_dir = real_smac_server
    # The SAME $HOME the server itself is using (spec: "session:
    # ~/.config/smac/" -- a real single-machine install shares one
    # account's home directory between `smac` and `smac-server`). This is
    # also exactly what "relaunch, same HOME" (Frame 8) needs: the second
    # `SmacApp` instance below must resolve the identical session.json.
    monkeypatch.setattr(Path, "home", lambda: home_dir)

    api = SmacApi(url)
    app = SmacApp(api)
    workspace_name = "AI Finance Co"
    email = "vimal@example.com"

    async with app.run_test() as pilot:
        # -- Frame 1: welcome screen -----------------------------------
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        assert "Welcome to SMAC" in _body_text(app)

        # -- Frame 3: /register, account-only (Identity v2, spec §6) -----
        await pilot.press(*"/register")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "email")
        await pilot.press(*email)
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "password")
        await pilot.press(*_TEST_PASSWORD)
        await pilot.press("enter")

        # -- account created, no workspace yet ---------------------------
        await _wait_until(pilot, lambda: app.header_text == "SMAC — no workspace yet")
        assert "account created" in _body_text(app)
        assert "/workspace create <name>" in _body_text(app)

        # -- /workspace create: found the workspace ----------------------
        await pilot.press(*f"/workspace create {workspace_name}")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "first name")
        await pilot.press(*"Vimal")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "last name")
        await pilot.press(*"Raguraman")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: "visibility" in app.footer_input.placeholder)
        await pilot.press("enter")  # default: private

        # -- Frame 4: landed in #general ---------------------------------
        await _wait_until(
            pilot, lambda: app.header_text == f"{workspace_name} — #general"
        )
        assert f'workspace "{workspace_name}" founded' in _body_text(app)
        assert api.session is not None
        workspace_id = api.session.workspace_id
        bearer = api.session.access_token
        whoami = api.whoami()
        handle = whoami["handle"]
        general_id = app.current_channel_id
        assert general_id is not None

        # -- Frame 5: bare text sends -------------------------------------
        await pilot.press(*"hello from the human")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: "hello from the human" in _body_text(app))

        # A second identity: the analyst agent, replying live in #general,
        # mentioning the human.
        analyst = _register_agent(url, workspace_id, bearer, "analyst")
        _add_channel_member(url, workspace_id, bearer, general_id, analyst["member_id"])
        await _post_until_seen(
            pilot,
            lambda: _agent_post(
                url,
                workspace_id,
                general_id,
                analyst["api_key"],
                f"@{handle} on it — pulling the close prices",
            ),
            "pulling the close prices",
            app,
        )
        reply_line = next(l for l in app._log_lines if "pulling the close prices" in l)
        assert "analyst:" in reply_line
        assert f"@{handle}" in reply_line
        assert "<@" not in reply_line  # the raw mention token never leaks

        # -- Frame 6: a mention in ANOTHER channel rings the bell ---------
        reports = api.create_channel("reports")
        reports_id = reports["channel_id"]
        _add_channel_member(url, workspace_id, bearer, reports_id, whoami["member_id"])
        risk_bot = _register_agent(url, workspace_id, bearer, "risk-bot")
        _add_channel_member(
            url, workspace_id, bearer, reports_id, risk_bot["member_id"]
        )
        assert app.current_channel_name == "general"  # still here when it lands
        await _post_until_seen(
            pilot,
            lambda: _agent_post(
                url,
                workspace_id,
                reports_id,
                risk_bot["api_key"],
                f"@{handle} exposure above threshold",
            ),
            "you were mentioned",
            app,
        )
        bell_line = next(l for l in app._log_lines if "you were mentioned" in l)
        assert "#reports" in bell_line
        assert "@risk-bot" in bell_line

        # -- Frame 7: /channel reports -- header flips, history loads ----
        await pilot.press(*"/channel reports")
        await pilot.press("enter")
        await _wait_until(
            pilot, lambda: app.header_text == f"{workspace_name} — #reports"
        )
        await _wait_until(pilot, lambda: "exposure above threshold" in _body_text(app))

        def _reports_caught_up() -> bool:
            row = next(
                r for r in api.unreads()["unreads"] if r["channel_id"] == reports_id
            )
            return bool(row["unread_count"] == 0)

        await _wait_until(pilot, _reports_caught_up)

        # -- /channels: everything caught up ------------------------------
        lines_before = len(app._log_lines)
        await pilot.press(*"/channels")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: "switch: /channel" in _body_text(app))
        table_lines = app._log_lines[lines_before:]
        general_row = next(l for l in table_lines if "#general" in l)
        reports_row = next(l for l in table_lines if "#reports" in l)
        assert "caught up" in general_row
        assert "caught up" in reports_row
        assert "unread" not in general_row
        assert "unread" not in reports_row
        assert "(here)" in reports_row  # currently in #reports

        # -- /quit: clean shutdown, session kept --------------------------
        await pilot.press(*"/quit")
        await pilot.press("enter")
        await _wait_until(
            pilot, lambda: any("goodbye" in line for line in app._log_lines)
        )

    from smac_cli.paths import session_path

    assert session_path().exists()

    # -- Frame 8: relaunch -- a BRAND NEW app instance, same $HOME --------
    reloaded_session = Session.load(session_path())
    assert reloaded_session is not None
    api2 = SmacApi(reloaded_session.url, session=reloaded_session)
    app2 = SmacApp(api2)
    async with app2.run_test() as pilot2:
        await _wait_until(
            pilot2, lambda: app2.header_text == f"{workspace_name} — #general"
        )
        assert app2.current_channel_name == "general"
        # No login screen at all -- straight in.
        assert "Welcome to SMAC" not in _body_text(app2)
        assert "/register" not in _body_text(app2)


async def _press_line(pilot: Any, text: str) -> None:
    """Type `text` into the footer input and press enter -- one TUI "line"."""
    await pilot.press(*text)
    await pilot.press("enter")


async def _send_until_seen_live(
    sender_pilot: Any,
    text: str,
    receiver_pilot: Any,
    receiver_app: SmacApp,
    *,
    attempts: int = 6,
    per_attempt_timeout: float = 1.0,
) -> None:
    """Send `text` from the sender's TUI (bare keypresses, re-sent on each
    attempt) until it shows up in the RECEIVER's live feed -- the
    TUI-to-TUI equivalent of `_post_until_seen`'s httpx-to-TUI race guard
    above: a message sent the instant the receiver's own live feed thread
    starts can beat that thread's WebSocket handshake."""
    for _ in range(attempts):
        await _press_line(sender_pilot, text)
        deadline = time.monotonic() + per_attempt_timeout
        while time.monotonic() < deadline:
            if any(text in line for line in receiver_app._log_lines):
                return
            await receiver_pilot.pause(0.02)
    raise AssertionError(f"{text!r} never appeared live after {attempts} attempts")


@pytest.mark.anyio
async def test_alice_invites_bob_across_fresh_homes_journey(
    real_smac_server: tuple[str, Path],
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Alice -> Bob invite/join story (SMAC-30/SMAC-79, spec §0), two
    SEPARATE `$HOME`s sharing one real server -- the exact scenario that
    was impossible without Postman before Identity v2: Alice `/register`s
    an account, `/workspace create`s her own workspace, `/invite`s a
    shareable code; Bob, from a totally fresh machine (`$HOME`), never
    having talked to this server before, `/register`s his OWN account and
    `/join <code>`s straight into Alice's `#general`; Alice mentions Bob's
    handle live, Bob's feed shows it; Bob relaunches and lands straight
    back in (Frame-8 behavior), on his own `$HOME`, independent of
    Alice's.
    """
    url, alice_home = real_smac_server
    bob_home = tmp_path_factory.mktemp("smac-tui-bob-home")

    monkeypatch.setattr(Path, "home", lambda: alice_home)

    alice_api = SmacApi(url)
    alice_app = SmacApp(alice_api)
    bob_api = SmacApi(url)
    bob_app = SmacApp(bob_api)

    workspace_name = "Alice's Workspace"
    alice_email = "alice@example.com"
    bob_email = "bob@example.com"

    async with alice_app.run_test() as alice_pilot, bob_app.run_test() as bob_pilot:
        # -- Alice: /register (account-only), on alice_home --------------
        await _wait_until(
            alice_pilot,
            lambda: any("server:" in line for line in alice_app._log_lines),
        )
        await _press_line(alice_pilot, "/register")
        await _wait_until(
            alice_pilot, lambda: alice_app.footer_input.placeholder == "email"
        )
        await _press_line(alice_pilot, alice_email)
        await _wait_until(
            alice_pilot, lambda: alice_app.footer_input.placeholder == "password"
        )
        await _press_line(alice_pilot, _TEST_PASSWORD)
        await _wait_until(
            alice_pilot, lambda: alice_app.header_text == "SMAC — no workspace yet"
        )

        # -- Alice: /workspace create -- founds her own workspace ---------
        await _press_line(alice_pilot, f"/workspace create {workspace_name}")
        await _wait_until(
            alice_pilot, lambda: alice_app.footer_input.placeholder == "first name"
        )
        await _press_line(alice_pilot, "Alice")
        await _wait_until(
            alice_pilot, lambda: alice_app.footer_input.placeholder == "last name"
        )
        await _press_line(alice_pilot, "Founder")
        await _wait_until(
            alice_pilot, lambda: "visibility" in alice_app.footer_input.placeholder
        )
        await alice_pilot.press("enter")  # default: private
        await _wait_until(
            alice_pilot, lambda: alice_app.header_text == f"{workspace_name} — #general"
        )
        alice_handle = alice_api.whoami()["handle"]

        # -- Alice: /invite -- mint a shareable code, capture it from the
        #    body output (exactly as a real user would read it) ----------
        lines_before_invite = len(alice_app._log_lines)
        await _press_line(alice_pilot, "/invite")
        await _wait_until(
            alice_pilot,
            lambda: any(
                "invite code:" in line
                for line in alice_app._log_lines[lines_before_invite:]
            ),
        )
        invite_line = next(
            line
            for line in alice_app._log_lines[lines_before_invite:]
            if "invite code:" in line
        )
        invite_code = invite_line.split("invite code:", 1)[1].strip()
        assert invite_code

        # -- Bob: a totally fresh $HOME from here on ----------------------
        monkeypatch.setattr(Path, "home", lambda: bob_home)

        # -- Bob: /register (his OWN account -- never talked to this
        #    server before) --------------------------------------------
        await _wait_until(
            bob_pilot, lambda: any("server:" in line for line in bob_app._log_lines)
        )
        await _press_line(bob_pilot, "/register")
        await _wait_until(
            bob_pilot, lambda: bob_app.footer_input.placeholder == "email"
        )
        await _press_line(bob_pilot, bob_email)
        await _wait_until(
            bob_pilot, lambda: bob_app.footer_input.placeholder == "password"
        )
        await _press_line(bob_pilot, _TEST_PASSWORD)
        await _wait_until(
            bob_pilot, lambda: bob_app.header_text == "SMAC — no workspace yet"
        )

        # -- Bob: /join <code> -- lands straight in Alice's #general ------
        await _press_line(bob_pilot, f"/join {invite_code}")
        await _wait_until(
            bob_pilot, lambda: bob_app.footer_input.placeholder == "first name"
        )
        await _press_line(bob_pilot, "Bob")
        await _wait_until(
            bob_pilot, lambda: bob_app.footer_input.placeholder == "last name"
        )
        await _press_line(bob_pilot, "Joiner")
        await _wait_until(
            bob_pilot, lambda: bob_app.header_text == f"{workspace_name} — #general"
        )
        assert bob_app.current_channel_name == "general"
        assert f'joined "{workspace_name}"' in _body_text(bob_app)
        bob_handle = bob_api.whoami()["handle"]
        assert bob_handle != alice_handle

        # -- Alice mentions Bob's handle live -- Bob's feed shows it -------
        mention_text = f"@{bob_handle} welcome to the team"
        await _send_until_seen_live(alice_pilot, mention_text, bob_pilot, bob_app)
        seen_line = next(l for l in bob_app._log_lines if mention_text in l)
        assert f"{alice_handle}:" in seen_line
        assert f"@{bob_handle}" in seen_line
        assert "<@" not in seen_line  # the raw mention token never leaks

        # -- Bob: /quit -- clean shutdown, session kept on bob_home --------
        await _press_line(bob_pilot, "/quit")
        await _wait_until(
            bob_pilot, lambda: any("goodbye" in line for line in bob_app._log_lines)
        )

    from smac_cli.paths import session_path

    # Path.home is still bob_home here (never switched back) -- Bob's own
    # session lives entirely under his own $HOME, independent of Alice's.
    assert session_path().exists()

    # -- Bob relaunches -- a BRAND NEW app instance, same (bob) $HOME:
    #    straight back in, no login screen at all (Frame-8 behavior) ------
    reloaded_bob_session = Session.load(session_path())
    assert reloaded_bob_session is not None
    bob_api2 = SmacApi(reloaded_bob_session.url, session=reloaded_bob_session)
    bob_app2 = SmacApp(bob_api2)
    async with bob_app2.run_test() as bob_pilot2:
        await _wait_until(
            bob_pilot2,
            lambda: bob_app2.header_text == f"{workspace_name} — #general",
        )
        assert bob_app2.current_channel_name == "general"
        assert "Welcome to SMAC" not in _body_text(bob_app2)
        assert "/register" not in _body_text(bob_app2)
