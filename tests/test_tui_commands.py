"""The remaining `smac_cli.commands` handlers (SMAC-72 task 6): `/whoami`,
`/channels` + `/unreads`, `/channel create`, `/workspace delete`, `/help`,
`/quit`, and Ctrl+C -- drawn against spec §0.2's frames (each frame IS the
expected output for its command).

Two layers, matching the pattern `test_tui_shell.py` (task 4) and
`test_tui_live.py` (task 5) already established:

1. Fast, deterministic `FakeApi` + `Pilot` tests for branches that don't
   need a real server: usage errors, the workspace-delete form's abort
   paths (Esc, name mismatch, wrong confirmation word), and Ctrl+C.
   `FakeApi` is imported from `test_tui_shell` rather than re-declared --
   same stub, one definition.
2. Real-`smac-server` + `Pilot` tests (`real_smac_server`, the
   module-scoped fixture `tests/conftest.py` already shares across the
   TUI test modules) for everything that needs authentic server behavior:
   `/whoami`'s `role`/workspace-visibility (SMAC-72 task 6 added the
   latter; SMAC-92 replaced the old boolean `is_admin` with `role` -- see
   `app.schemas.MemberSelfOut`'s docstring),
   `/channels`' real unread/mention counts, `/channel create`'s real
   switch and its real 409 envelope (verbatim), and `/workspace
   delete` actually exercising `SmacApi.delete_workspace()` end to end --
   the brief calls this out explicitly as a method with no dedicated unit
   test before this task.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from smac_cli.api import Session, SmacApi
from smac_cli.app import SmacApp
from smac_cli.errors import NameTakenError, SmacError
from smac_cli.paths import session_path
from tests.test_tui_shell import FakeApi

_TEST_PASSWORD = "test-password-123"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _wait_until(pilot: Any, predicate: Any, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout}s")
        await pilot.pause(0.02)


def _body_text(app: SmacApp) -> str:
    return "\n".join(app._log_lines)


async def _run_command(pilot: Any, text: str) -> None:
    await pilot.press(*text)
    await pilot.press("enter")


def _logged_in_fake() -> FakeApi:
    session = Session(
        url="http://fake.example",
        email="vimal@example.com",
        account_access_token="aat",
        account_refresh_token="art",
        workspace_id="ws-1",
        access_token="at",
        refresh_token="rt",
    )
    fake = FakeApi(session=session)
    return fake


def _app_with(fake: FakeApi) -> SmacApp:
    """A `SmacApp` that will restore straight into "AI Finance Co" on
    startup (Frame 8) -- caches the name for `fake.session.workspace_id`
    the same way a real `/register`/`/login` would have (`smac_cli.app.
    cache_workspace_name`), since `SmacApp._restore_session` looks it up
    from that sidecar rather than trusting a value set directly on the
    not-yet-mounted app instance (which `_restore_session` overwrites)."""
    from smac_cli.app import cache_workspace_name

    assert fake.session is not None
    cache_workspace_name(fake.session.workspace_id, "AI Finance Co")
    return SmacApp(fake)


# --------------------------------------------------------------------------
# FakeApi: /whoami
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_whoami_shows_name_handle_admin_and_workspace() -> None:
    fake = _logged_in_fake()
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _run_command(pilot, "/whoami")
        await _wait_until(pilot, lambda: "you:" in _body_text(app))
        text = _body_text(app)
        assert "you: Vimal Raguraman (@vraguraman) · admin" in text
        assert "workspace: AI Finance Co (private)" in text


@pytest.mark.anyio
async def test_whoami_non_admin_has_no_admin_suffix() -> None:
    fake = _logged_in_fake()
    fake.whoami_result["role"] = "member"
    fake.whoami_result["workspace_visibility"] = "public"
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _run_command(pilot, "/whoami")
        await _wait_until(pilot, lambda: "you:" in _body_text(app))
        text = _body_text(app)
        assert "you: Vimal Raguraman (@vraguraman)" in text
        assert "· admin" not in text
        assert "workspace: AI Finance Co (public)" in text


@pytest.mark.anyio
async def test_whoami_agent_admin_shows_role_suffix() -> None:
    """SMAC-92: any non-`member` role renders verbatim, not just `admin`."""
    fake = _logged_in_fake()
    fake.whoami_result["role"] = "agent_admin"
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _run_command(pilot, "/whoami")
        await _wait_until(pilot, lambda: "you:" in _body_text(app))
        text = _body_text(app)
        assert "you: Vimal Raguraman (@vraguraman) · agent_admin" in text


# --------------------------------------------------------------------------
# FakeApi: /channels + /unreads
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_channels_table_shows_here_unread_and_mention_badge() -> None:
    fake = _logged_in_fake()
    fake.unreads_result = {
        "unreads": [
            {
                "channel_id": "general-id",
                "channel_name": "general",
                "unread_count": 0,
                "mention_count": 0,
            },
            {
                "channel_id": "reports-id",
                "channel_name": "reports",
                "unread_count": 4,
                "mention_count": 1,
            },
            {
                "channel_id": "research-id",
                "channel_name": "research",
                "unread_count": 12,
                "mention_count": 0,
            },
        ]
    }
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _run_command(pilot, "/channels")
        await _wait_until(pilot, lambda: "switch: /channel" in _body_text(app))
        text = _body_text(app)
        assert "#general    ·  caught up  (here)" in text
        assert "#reports    ·  4 unread  🔔 1 mention" in text
        assert "#research    ·  12 unread" in text
        assert "🔔 1 mentions" not in text  # singular for exactly 1


@pytest.mark.anyio
async def test_unreads_is_the_same_handler_as_channels() -> None:
    fake = _logged_in_fake()
    fake.unreads_result = {
        "unreads": [
            {
                "channel_id": "general-id",
                "channel_name": "general",
                "unread_count": 0,
                "mention_count": 0,
            }
        ]
    }
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _run_command(pilot, "/unreads")
        await _wait_until(pilot, lambda: "switch: /channel" in _body_text(app))
        assert "#general    ·  caught up  (here)" in _body_text(app)


@pytest.mark.anyio
async def test_channels_pullup_lists_both_names() -> None:
    app = SmacApp(FakeApi())
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await pilot.press("/")
        await _wait_until(pilot, lambda: app.pullup.display)
        ids = {
            str(app.pullup.get_option_at_index(i).id)
            for i in range(app.pullup.option_count)
        }
        assert "channels" in ids
        assert "unreads" in ids
        assert "whoami" in ids
        assert "workspace" in ids


# --------------------------------------------------------------------------
# FakeApi: /channel create
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_channel_create_switches_and_updates_header() -> None:
    fake = _logged_in_fake()
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _run_command(pilot, "/channel create reports")
        await _wait_until(pilot, lambda: app.header_text.endswith("#reports"))
        assert fake.create_channel_calls == ["reports"]
        assert app.current_channel_name == "reports"
        assert "channel #reports created — you're in it" in _body_text(app)


@pytest.mark.anyio
async def test_channel_create_no_name_shows_usage() -> None:
    fake = _logged_in_fake()
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _run_command(pilot, "/channel create")
        await _wait_until(
            pilot, lambda: "usage: /channel create <name>" in _body_text(app)
        )
        assert fake.create_channel_calls == []


@pytest.mark.anyio
async def test_channel_create_conflict_renders_server_envelope_verbatim() -> None:
    """Spec §0.2's frame shows the full `code: message` envelope for this
    one error, not just the message (unlike every other command)."""
    fake = _logged_in_fake()
    fake.create_channel_error = NameTakenError(
        "channel_name_taken",
        "A channel named 'Reports' already exists in this workspace",
    )
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        before_channel = app.current_channel_name
        await _run_command(pilot, "/channel create Reports")
        await _wait_until(
            pilot,
            lambda: "already exists in this workspace" in _body_text(app),
        )
        text = _body_text(app)
        assert (
            "channel_name_taken: A channel named 'Reports' already exists "
            "in this workspace" in text
        )
        # The failed create never switched the channel.
        assert app.current_channel_name == before_channel


@pytest.mark.anyio
async def test_channel_create_non_conflict_error_renders_message_only() -> None:
    """Only `NameTakenError` (the 409 case) gets the code-prefixed
    rendering -- every other `SmacError` during `/channel create` falls
    through to `SmacApp._run_command`'s ordinary message-only handling,
    same as every other command in this app."""
    fake = _logged_in_fake()
    fake.create_channel_error = SmacError("rate_limited", "Posting too fast")
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        before_channel = app.current_channel_name
        await _run_command(pilot, "/channel create Reports")
        await _wait_until(pilot, lambda: "Posting too fast" in _body_text(app))
        text = _body_text(app)
        assert "Posting too fast" in text
        assert "rate_limited:" not in text
        assert "rate_limited" not in text
        # The failed create never switched the channel.
        assert app.current_channel_name == before_channel


# --------------------------------------------------------------------------
# FakeApi: /workspace delete -- the two-step typed confirmation
# --------------------------------------------------------------------------


async def _start_workspace_delete(pilot: Any, app: SmacApp) -> None:
    await _run_command(pilot, "/workspace delete")
    await _wait_until(pilot, lambda: app.footer_input.placeholder == "name")


@pytest.mark.anyio
async def test_workspace_delete_wrong_name_aborts() -> None:
    fake = _logged_in_fake()
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _start_workspace_delete(pilot, app)
        await pilot.press(*"Wrong Name")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: "did not match" in _body_text(app))
        assert fake.delete_workspace_calls == 0
        assert fake.session is not None  # never cleared
        assert app.workspace_name == "AI Finance Co"


@pytest.mark.anyio
async def test_workspace_delete_wrong_confirmation_word_aborts() -> None:
    fake = _logged_in_fake()
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _start_workspace_delete(pilot, app)
        await pilot.press(*"AI Finance Co")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "confirm")
        await pilot.press(*"yes please")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: "cancelled" in _body_text(app))
        assert fake.delete_workspace_calls == 0
        assert fake.session is not None


@pytest.mark.anyio
async def test_workspace_delete_escape_cancels_without_calling_api() -> None:
    fake = _logged_in_fake()
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _start_workspace_delete(pilot, app)
        await pilot.press("escape")
        await _wait_until(pilot, lambda: not app.footer_input.password)
        assert fake.delete_workspace_calls == 0
        assert fake.session is not None
        assert app.workspace_name == "AI Finance Co"  # never touched


@pytest.mark.anyio
async def test_workspace_delete_success_clears_session_and_shows_welcome() -> None:
    fake = _logged_in_fake()
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _start_workspace_delete(pilot, app)
        await pilot.press(*"AI Finance Co")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "confirm")
        await pilot.press(*"delete")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.header_text == "SMAC — not logged in")
        assert fake.delete_workspace_calls == 1
        assert fake.session is None
        assert app.workspace_name is None
        assert app.current_channel_id is None
        text = _body_text(app)
        assert 'workspace "AI Finance Co" deleted' in text
        assert "Welcome to SMAC" in text


# --------------------------------------------------------------------------
# /invite: mint a shareable code, print the exact line to tell an invitee
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_invite_prints_code_and_join_hint() -> None:
    fake = _logged_in_fake()
    fake.mint_invite_result = {
        "invite_id": "inv-1",
        "invite_type": "code",
        "code": "abc123",
    }
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _run_command(pilot, "/invite")
        await _wait_until(pilot, lambda: "invite code:" in _body_text(app))
        text = _body_text(app)
        assert "invite code: abc123" in text
        assert "smac → /register → /join abc123" in text


@pytest.mark.anyio
async def test_invite_not_admin_shows_server_message() -> None:
    """The client mints no code of its own; a non-admin's mint attempt
    fails server-side and the message-only rejection surfaces same as
    every other command's generic `SmacError` handling."""
    from smac_cli.errors import SmacError as GenericSmacError

    fake = _logged_in_fake()
    fake.mint_invite_error = GenericSmacError(
        "not_workspace_admin", "Only a workspace admin may do this"
    )
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _run_command(pilot, "/invite")
        await _wait_until(
            pilot, lambda: "Only a workspace admin may do this" in _body_text(app)
        )


# --------------------------------------------------------------------------
# /join <code>: redeem a code from inside an already-active session
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_join_code_from_within_a_workspace_enters_the_new_one() -> None:
    """An account can belong to several workspaces -- `/join <code>` works
    the same whether typed from the no-workspace state or from inside an
    already-active workspace."""
    fake = _logged_in_fake()
    fake.join_code_target = ("ws-2", "Second Workspace")
    app = _app_with(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)

        await pilot.press(*"/join xyz789")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "first name")
        await pilot.press(*"New")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "last name")
        await pilot.press(*"Member")
        await pilot.press("enter")

        await _wait_until(
            pilot, lambda: app.header_text == "Second Workspace — #general"
        )
        assert fake.session is not None
        assert fake.session.workspace_id == "ws-2"


# --------------------------------------------------------------------------
# /help, Ctrl+C
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_help_describes_every_command_per_the_frame() -> None:
    app = SmacApp(FakeApi())
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await _run_command(pilot, "/help")
        await _wait_until(
            pilot, lambda: any("commands" in line for line in app._log_lines)
        )
        text = _body_text(app)
        for expected in (
            "/register",
            "/workspace create <name>",
            "/join <code>",
            "/login",
            "/invite",
            "/whoami",
            "/channels /unreads",
            "/channel <name>",
            "/channel create <name>",
            "/workspace delete",
            "/quit",
        ):
            assert expected in text, f"missing {expected!r} from /help"
        assert "anything without / is a message to" in text


@pytest.mark.anyio
async def test_ctrl_c_is_the_same_as_quit() -> None:
    app = SmacApp(FakeApi())
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await pilot.press("ctrl+c")
        await _wait_until(
            pilot, lambda: any("goodbye" in line for line in app._log_lines)
        )


# --------------------------------------------------------------------------
# Real server: /whoami, /channels, /channel create (+ 409), /workspace
# delete (SmacApi.delete_workspace() has no other test), /quit.
# --------------------------------------------------------------------------


def _found_workspace(url: str, *, visibility: str = "private") -> SmacApi:
    """Signup + `/workspace create`'s API-level equivalent: a fresh
    account, founding a brand-new workspace as its admin (Identity v2,
    spec §3 -- two calls now, `signup` then `create_workspace`, where the
    retired `register_found` used to be one)."""
    api = SmacApi(url)
    api.signup(f"{_unique('founder')}@test.example", _TEST_PASSWORD)
    api.create_workspace(_unique("wksp"), visibility, "Ada", "Lovelace")
    return api


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
async def test_whoami_against_real_server_shows_founder_admin_and_visibility(
    real_smac_server: tuple[str, Path],
) -> None:
    url, _home_dir = real_smac_server
    founder = _found_workspace(url, visibility="private")
    assert founder.session is not None
    app = SmacApp(SmacApi(url, session=replace(founder.session)))
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _run_command(pilot, "/whoami")
        await _wait_until(pilot, lambda: "you:" in _body_text(app))
        text = _body_text(app)
        assert "Ada Lovelace" in text
        assert "· admin" in text
        assert "(private)" in text


@pytest.mark.anyio
async def test_channels_against_real_server_reflects_real_unreads(
    real_smac_server: tuple[str, Path],
) -> None:
    url, _home_dir = real_smac_server
    founder = _found_workspace(url)
    assert founder.session is not None
    workspace_id = founder.session.workspace_id
    bearer = founder.session.access_token
    whoami = founder.whoami()

    reports = founder.create_channel(_unique("reports"))
    _add_channel_member(
        url, workspace_id, bearer, reports["channel_id"], whoami["member_id"]
    )
    agent = _register_agent(url, workspace_id, bearer, "risk-bot")
    _add_channel_member(
        url, workspace_id, bearer, reports["channel_id"], agent["member_id"]
    )
    _agent_post(
        url,
        workspace_id,
        reports["channel_id"],
        agent["api_key"],
        f"@{whoami['handle']} exposure above threshold",
    )

    app = SmacApp(SmacApi(url, session=replace(founder.session)))
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _run_command(pilot, "/channels")
        await _wait_until(pilot, lambda: "switch: /channel" in _body_text(app))
        text = _body_text(app)
        assert "#general" in text
        assert "(here)" in text
        assert f"#{reports['channel_name']}" in text
        assert "1 unread" in text
        assert "🔔 1 mention" in text


@pytest.mark.anyio
async def test_channel_create_against_real_server_switches_and_409_verbatim(
    real_smac_server: tuple[str, Path],
) -> None:
    url, _home_dir = real_smac_server
    founder = _found_workspace(url)
    assert founder.session is not None
    app = SmacApp(SmacApi(url, session=replace(founder.session)))
    name = _unique("reports")
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)

        await _run_command(pilot, f"/channel create {name}")
        await _wait_until(pilot, lambda: app.header_text.endswith(f"#{name}"))
        assert "created — you're in it" in _body_text(app)

        # Duplicate (server compares case-insensitively -- SMAC-68): the
        # server's own `code: message` envelope renders verbatim.
        await _run_command(pilot, f"/channel create {name.upper()}")
        await _wait_until(
            pilot, lambda: "already exists in this workspace" in _body_text(app)
        )
        text = _body_text(app)
        assert "channel_name_taken:" in text
        assert f"A channel named '{name.upper()}' already exists" in text
        # Still in the channel from the successful create -- the failed
        # duplicate never switched anything.
        assert app.current_channel_name == name


@pytest.mark.anyio
async def test_workspace_delete_against_real_server_deletes_and_resets(
    real_smac_server: tuple[str, Path],
) -> None:
    """`SmacApi.delete_workspace()` (spec §2, `DELETE /workspaces/{id}
    ?confirm=delete`) had no dedicated unit test before this task -- this
    is that coverage, exercised end to end through the `/workspace
    delete` command against a real server."""
    url, _home_dir = real_smac_server
    workspace_name = _unique("wksp-real")
    founder = SmacApi(url)
    founder.signup(f"{_unique('founder')}@test.example", _TEST_PASSWORD)
    founder.create_workspace(workspace_name, "private", "Ada", "Lovelace")
    assert founder.session is not None

    from smac_cli.app import cache_workspace_name

    cache_workspace_name(founder.session.workspace_id, workspace_name)
    app = SmacApp(SmacApi(url, session=replace(founder.session)))

    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)

        await _start_workspace_delete(pilot, app)
        await pilot.press(*workspace_name)
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "confirm")
        await pilot.press(*"delete")
        await pilot.press("enter")

        await _wait_until(pilot, lambda: app.header_text == "SMAC — not logged in")
        assert f'workspace "{workspace_name}" deleted' in _body_text(app)
        assert "Welcome to SMAC" in _body_text(app)
        assert not session_path().exists()

    # The workspace is really gone: the founder's own session can no
    # longer resolve a member profile (its member row was deleted along
    # with everything else in the cascade).
    with pytest.raises(SmacError):
        founder.whoami()


@pytest.mark.anyio
async def test_quit_against_real_server_is_clean_and_keeps_session(
    real_smac_server: tuple[str, Path],
) -> None:
    url, _home_dir = real_smac_server
    founder = _found_workspace(url)
    assert founder.session is not None
    app = SmacApp(SmacApi(url, session=replace(founder.session)))
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        assert session_path().exists()

        await _run_command(pilot, "/quit")
        await _wait_until(
            pilot, lambda: any("goodbye" in line for line in app._log_lines)
        )

    # `on_unmount` (both live-room background threads stopped) is only
    # guaranteed once Textual's own teardown pass runs -- reached here as
    # the `async with app.run_test()` block above exits, matching
    # `SmacApp.on_unmount`'s own docstring ("...or a test's run_test()
    # tearing down").
    assert app._channel_feed is None
    assert app._event_bell is None

    # The whole point of `/quit` (vs. `/workspace delete`): the saved
    # session is untouched, ready for a straight-in relaunch.
    assert session_path().exists()
    loaded = Session.load(session_path())
    assert loaded is not None
    assert loaded.workspace_id == founder.session.workspace_id
