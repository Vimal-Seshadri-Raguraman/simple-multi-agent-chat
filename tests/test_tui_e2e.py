"""The end-to-end human journey (SMAC-72 task 6, final): spec §0.1's
storyline, start to finish, against a REAL spawned `smac-server` and the
real `SmacApp` driven through Textual's own `App.run_test()`/`Pilot` --
no `FakeApi` anywhere in this module.

register -> lands in #general (Frame 4) -> bare-text send (Frame 5) ->
a second identity (an agent, minted via `POST /members/agents` -- the
TUI itself has no agent-auth) replies mentioning the human, live ->
a mention in ANOTHER channel rings the bell (Frame 6) -> `/channel
<name>` switches (Frame 7) -> `/channels` shows everything caught up ->
`/quit` (session kept) -> a BRAND NEW `SmacApp`/`SmacApi` pair, same
`$HOME`, lands straight into #general with no login screen at all
(Frame 8) -- "every day after starts at Frame 8" (spec §0.1's closing
line).

Deliberately one long test rather than many small ones: the whole point
of an e2e test is that each step's state (the session, the channel
memberships, the read cursors) is exactly what the PREVIOUS step left
behind, not a fixture's clean-room setup.
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

        # -- Frame 3: /register, the two-step form ----------------------
        await pilot.press(*"/register")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "email")
        await pilot.press(*email)
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "password")
        await pilot.press(*_TEST_PASSWORD)
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "first name")
        await pilot.press(*"Vimal")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "last name")
        await pilot.press(*"Raguraman")
        await pilot.press("enter")
        await _wait_until(
            pilot, lambda: app.footer_input.placeholder == "workspace name"
        )
        await pilot.press(*workspace_name)
        await pilot.press("enter")
        await _wait_until(pilot, lambda: "visibility" in app.footer_input.placeholder)
        await pilot.press("enter")  # default: private

        # -- Frame 4: landed in #general ---------------------------------
        await _wait_until(
            pilot, lambda: app.header_text == f"{workspace_name} — #general"
        )
        assert "account created" in _body_text(app)
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
