"""`smac_cli.app.SmacApp` + `smac_cli.commands`: the TUI shell.

Drives the real `SmacApp` through Textual's own test harness
(`App.run_test()` + `Pilot`) against a hand-rolled `FakeApi` that
implements every `SmacApi` method name the shell calls -- no real server,
no real HTTP. `Path.home` is monkeypatched to `tmp_path` (the same
pattern `test_tui_api.py` uses) so the workspace-name sidecar cache never
touches a developer's real `~/.config/smac`.

Because every `SmacApi` call runs on a `run_worker(thread=True)` worker
(the whole point of the design -- see `smac_cli/app.py`'s module
docstring), these tests can't just `await pilot.press(...)` and assume
the effect landed: the effect happens on a background OS thread that
gets scheduled independently. `_wait_until` polls (with `pilot.pause`,
which actually yields wall-clock time slices) until the expected UI
state shows up, or times out.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import pytest

from smac_cli import CLIENT_VERSION
from smac_cli.api import Session
from smac_cli.app import SmacApp, cache_workspace_name
from smac_cli.errors import SessionExpired, Unreachable


@pytest.fixture
def anyio_backend() -> str:
    """Pin the `anyio` pytest plugin (already installed transitively via
    httpx/fastapi) to the asyncio backend -- the only one installed."""
    return "asyncio"


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect `Path.home()` so the workspace-name cache never touches a
    developer's real `~/.config/smac`."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


class FakeApi:
    """A stub implementing every `SmacApi` method name the shell calls.

    Mirrors `SmacApi`'s real behavior just closely enough for the shell's
    logic to be exercised: `login`/`register_found`/`register_into` all
    set `self.session` and return it, exactly like the real client.
    """

    def __init__(
        self, *, session: Session | None = None, server_version: str = CLIENT_VERSION
    ) -> None:
        self.url = "http://fake.example"
        self.session = session
        self.server_version = server_version
        self.meta_error: Exception | None = None
        self.discover_result: list[dict[str, str]] = []
        self.search_result: list[dict[str, str]] = []
        self.whoami_result: dict[str, Any] = {"handle": "vraguraman", "member_id": "m1"}
        self.whoami_error: Exception | None = None
        self.posts: list[tuple[str, str]] = []
        self.search_calls: list[str] = []
        self._next_workspace_id = 0

    def meta(self) -> dict[str, Any]:
        if self.meta_error is not None:
            raise self.meta_error
        return {"server_version": self.server_version, "api_version": 1}

    def discover(self, email: str, password: str) -> list[dict[str, str]]:
        return self.discover_result

    def _new_session(self, workspace_id: str, email: str) -> Session:
        session = Session(
            url=self.url,
            workspace_id=workspace_id,
            access_token="at",
            refresh_token="rt",
            email=email,
        )
        self.session = session
        return session

    def login(self, workspace_id: str, email: str, password: str) -> Session:
        return self._new_session(workspace_id, email)

    def register_found(
        self,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        workspace_name: str,
        visibility: str,
    ) -> Session:
        self._next_workspace_id += 1
        return self._new_session(f"ws-{self._next_workspace_id}", email)

    def register_into(
        self,
        workspace_id: str,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
    ) -> Session:
        return self._new_session(workspace_id, email)

    def search_public(self, q: str = "") -> list[dict[str, str]]:
        self.search_calls.append(q)
        q_lower = q.lower()
        return [w for w in self.search_result if q_lower in w["workspace_name"].lower()]

    def whoami(self) -> dict[str, Any]:
        if self.whoami_error is not None:
            raise self.whoami_error
        return self.whoami_result

    def post(self, channel_id: str, text: str) -> dict[str, Any]:
        self.posts.append((channel_id, text))
        return {}


async def _wait_until(
    pilot: Any, predicate: Callable[[], bool], *, timeout: float = 3.0
) -> None:
    """Poll `predicate` (giving the background worker thread real
    wall-clock time via `pilot.pause`) until it's true, or raise."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout}s")
        await pilot.pause(0.01)


def _body_text(app: SmacApp) -> str:
    return "\n".join(app._log_lines)


# --------------------------------------------------------------------------
# Welcome screen / startup states
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_welcome_screen_shows_commands_and_server_status() -> None:
    app = SmacApp(FakeApi())
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        text = _body_text(app)
        assert "/register" in text
        assert "/login" in text
        assert "server: http://fake.example" in text
        assert "running (v" in text
        assert app.header_text == "SMAC — not logged in"


@pytest.mark.anyio
async def test_server_unreachable_shows_not_reachable_status() -> None:
    fake = FakeApi()
    fake.meta_error = Unreachable(fake.url)
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("not reachable" in line for line in app._log_lines)
        )
        assert "smac-server --start" in _body_text(app)


@pytest.mark.anyio
async def test_version_mismatch_shows_update_system_line() -> None:
    fake = FakeApi(server_version="9.9.9")
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("update: git pull" in line for line in app._log_lines)
        )
        text = _body_text(app)
        assert f"server 9.9.9, client {CLIENT_VERSION}" in text


@pytest.mark.anyio
async def test_matching_version_shows_no_update_line() -> None:
    app = SmacApp(FakeApi(server_version=CLIENT_VERSION))
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        assert "update: git pull" not in _body_text(app)


@pytest.mark.anyio
async def test_session_restore_lands_in_general_with_cached_name(
    tmp_path: Path,
) -> None:
    session = Session(
        url="http://fake.example",
        workspace_id="ws-cached",
        access_token="at",
        refresh_token="rt",
        email="vimal@example.com",
    )
    cache_workspace_name("ws-cached", "AI Finance Co")
    app = SmacApp(FakeApi(session=session))
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.header_text == "AI Finance Co — #general")
        assert app.current_channel_name == "general"
        # No welcome banner: a restored valid session skips it entirely.
        assert "Welcome to SMAC" not in _body_text(app)


@pytest.mark.anyio
async def test_session_expired_falls_back_to_welcome_screen() -> None:
    session = Session(
        url="http://fake.example",
        workspace_id="ws-1",
        access_token="at",
        refresh_token="rt",
        email="vimal@example.com",
    )
    fake = FakeApi(session=session)
    fake.whoami_error = SessionExpired()
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("session expired" in line for line in app._log_lines)
        )
        assert app.header_text == "SMAC — not logged in"
        assert "Welcome to SMAC" in _body_text(app)


# --------------------------------------------------------------------------
# Footer input contract: pull-up + dispatch
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_slash_shows_pullup_filters_and_escape_dismisses() -> None:
    app = SmacApp(FakeApi())
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )

        await pilot.press("/")
        await _wait_until(pilot, lambda: app.pullup.display)
        assert app.pullup.option_count == 4  # register, login, help, quit

        await pilot.press(*"re")
        await _wait_until(pilot, lambda: app.pullup.option_count == 1)
        assert str(app.pullup.get_option_at_index(0).id) == "register"

        await pilot.press("escape")
        await _wait_until(pilot, lambda: not app.pullup.display)
        # Escape dismisses the suggestions but doesn't clear the draft.
        assert app.footer_input.value == "/re"


@pytest.mark.anyio
async def test_unknown_command_shows_system_line() -> None:
    app = SmacApp(FakeApi())
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await pilot.press(*"/bogus")
        await pilot.press("enter")
        await _wait_until(
            pilot,
            lambda: any("unknown command: /bogus" in line for line in app._log_lines),
        )


@pytest.mark.anyio
async def test_bare_text_logged_out_shows_not_logged_in_line() -> None:
    app = SmacApp(FakeApi())
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await pilot.press(*"hello there")
        await pilot.press("enter")
        await _wait_until(
            pilot, lambda: any("not logged in" in line for line in app._log_lines)
        )


@pytest.mark.anyio
async def test_empty_enter_is_a_no_op() -> None:
    app = SmacApp(FakeApi())
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        lines_before = len(app._log_lines)
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert len(app._log_lines) == lines_before


# --------------------------------------------------------------------------
# /register: the two-step form
# --------------------------------------------------------------------------


async def _run_register(
    pilot: Any, app: SmacApp, *, visibility: str | None = None
) -> None:
    await pilot.press("/")
    await _wait_until(pilot, lambda: app.pullup.display)
    await pilot.press(*"register")
    await pilot.press("enter")

    await _wait_until(pilot, lambda: app.footer_input.placeholder == "email")
    await pilot.press(*"vimal@example.com")
    await pilot.press("enter")

    await _wait_until(pilot, lambda: app.footer_input.placeholder == "password")
    assert app.footer_input.password is True
    await pilot.press(*"hunter2-pass")
    await pilot.press("enter")

    await _wait_until(pilot, lambda: app.footer_input.placeholder == "first name")
    assert app.footer_input.password is False
    await pilot.press(*"Vimal")
    await pilot.press("enter")

    await _wait_until(pilot, lambda: app.footer_input.placeholder == "last name")
    await pilot.press(*"Raguraman")
    await pilot.press("enter")

    await _wait_until(pilot, lambda: app.footer_input.placeholder == "workspace name")
    await pilot.press(*"AI Finance Co")
    await pilot.press("enter")

    await _wait_until(pilot, lambda: "visibility" in app.footer_input.placeholder)
    if visibility is not None:
        await pilot.press(*visibility)
    await pilot.press("enter")


@pytest.mark.anyio
async def test_register_two_step_form_lands_in_general() -> None:
    fake = FakeApi()
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await _run_register(pilot, app)

        await _wait_until(pilot, lambda: app.header_text == "AI Finance Co — #general")
        assert app.current_channel_name == "general"
        text = _body_text(app)
        assert "step 1 of 2: create your account" in text
        assert "step 2 of 2: your workspace" in text
        # Account-created banner precedes the workspace-founded banner
        # (spec Frame 4's order), and both precede the header settling.
        assert text.index("account created") < text.index(
            'workspace "AI Finance Co" founded'
        )
        assert "@vraguraman" in text
        assert fake.session is not None


@pytest.mark.anyio
async def test_register_form_escape_cancels_and_resets_header() -> None:
    app = SmacApp(FakeApi())
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await pilot.press(*"/register")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "email")
        assert app.header_text == "SMAC — creating your account"

        await pilot.press("escape")
        await _wait_until(pilot, lambda: app.header_text == "SMAC — not logged in")
        assert app.api.session is None


# --------------------------------------------------------------------------
# /login: one-match, multi-match picker, zero-match join frame
# --------------------------------------------------------------------------


async def _start_login(pilot: Any, app: SmacApp, email: str, password: str) -> None:
    await pilot.press(*"/login")
    await pilot.press("enter")
    await _wait_until(pilot, lambda: app.footer_input.placeholder == "email")
    await pilot.press(*email)
    await pilot.press("enter")
    await _wait_until(pilot, lambda: app.footer_input.placeholder == "password")
    await pilot.press(*password)
    await pilot.press("enter")


@pytest.mark.anyio
async def test_login_one_match_auto_login_updates_header() -> None:
    fake = FakeApi()
    fake.discover_result = [{"workspace_id": "ws-1", "workspace_name": "AI Finance Co"}]
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await _start_login(pilot, app, "vimal@example.com", "pw")
        await _wait_until(pilot, lambda: app.header_text == "AI Finance Co — #general")
        assert fake.session is not None
        assert fake.session.workspace_id == "ws-1"


@pytest.mark.anyio
async def test_login_multi_match_shows_picker_with_both_names() -> None:
    fake = FakeApi()
    fake.discover_result = [
        {"workspace_id": "ws-1", "workspace_name": "AI Finance Co"},
        {"workspace_id": "ws-2", "workspace_name": "Research Lab"},
    ]
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await _start_login(pilot, app, "vimal@example.com", "pw")

        await _wait_until(pilot, lambda: app.header_text == "SMAC — choose a workspace")
        await _wait_until(pilot, lambda: app.pullup.option_count == 2)
        labels = {app.pullup.get_option_at_index(i).prompt for i in (0, 1)}
        assert any("AI Finance Co" in str(label) for label in labels)
        assert any("Research Lab" in str(label) for label in labels)
        # The "your accounts:" caption is a plain line (Frame 3b draws it
        # un-wrapped), not a dim "── ── " system line.
        assert "your accounts:" in app._log_lines
        assert "── your accounts: ──" not in app._log_lines

        # Select the second entry and confirm it logs into THAT workspace.
        await pilot.press("down")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.header_text == "Research Lab — #general")
        assert fake.session is not None
        assert fake.session.workspace_id == "ws-2"


@pytest.mark.anyio
async def test_login_multi_match_escape_cancels_and_resets_header() -> None:
    fake = FakeApi()
    fake.discover_result = [
        {"workspace_id": "ws-1", "workspace_name": "AI Finance Co"},
        {"workspace_id": "ws-2", "workspace_name": "Research Lab"},
    ]
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await _start_login(pilot, app, "vimal@example.com", "pw")
        await _wait_until(pilot, lambda: app.header_text == "SMAC — choose a workspace")
        await _wait_until(pilot, lambda: app.pullup.option_count == 2)

        await pilot.press("escape")
        await _wait_until(pilot, lambda: app.header_text == "SMAC — not logged in")
        assert app.api.session is None
        assert not app.pullup.display


@pytest.mark.anyio
async def test_login_zero_match_join_flow_filters_and_registers() -> None:
    fake = FakeApi()
    fake.discover_result = []
    fake.search_result = [
        {
            "workspace_id": "ws-pub-1",
            "workspace_name": "AI Finance Co",
            "visibility": "public",
        },
        {
            "workspace_id": "ws-pub-2",
            "workspace_name": "Open Research",
            "visibility": "public",
        },
    ]
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await _start_login(pilot, app, "new@example.com", "pw")

        await _wait_until(
            pilot, lambda: app.header_text == "SMAC — no workspace yet: join one"
        )
        await _wait_until(pilot, lambda: app.pullup.option_count == 2)
        # The caption and the "/register" hint are plain lines (Frame 3c
        # draws both un-wrapped), not dim "── ── " system lines.
        assert "public workspaces (type to search):" in app._log_lines
        assert "(or /register to create your own)" in app._log_lines
        assert "── public workspaces (type to search): ──" not in app._log_lines

        await pilot.press(*"fin")
        await _wait_until(pilot, lambda: app.pullup.option_count == 1)
        assert str(app.pullup.get_option_at_index(0).id) == "ws-pub-1"
        assert "fin" in fake.search_calls

        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "first name")
        await pilot.press(*"New")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "last name")
        await pilot.press(*"Member")
        await pilot.press("enter")

        await _wait_until(pilot, lambda: app.header_text == "AI Finance Co — #general")
        assert fake.session is not None
        assert fake.session.workspace_id == "ws-pub-1"


@pytest.mark.anyio
async def test_login_zero_match_join_frame_escape_cancels_and_resets_header() -> None:
    fake = FakeApi()
    fake.discover_result = []
    fake.search_result = [
        {
            "workspace_id": "ws-pub-1",
            "workspace_name": "AI Finance Co",
            "visibility": "public",
        },
    ]
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await _start_login(pilot, app, "new@example.com", "pw")
        await _wait_until(
            pilot, lambda: app.header_text == "SMAC — no workspace yet: join one"
        )
        await _wait_until(pilot, lambda: app.pullup.option_count == 1)

        await pilot.press("escape")
        await _wait_until(pilot, lambda: app.header_text == "SMAC — not logged in")
        assert app.api.session is None
        assert not app.pullup.display


# --------------------------------------------------------------------------
# /help, /quit
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_help_lists_registered_commands() -> None:
    app = SmacApp(FakeApi())
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await pilot.press(*"/help")
        await pilot.press("enter")
        await _wait_until(
            pilot, lambda: any("commands" in line for line in app._log_lines)
        )
        text = _body_text(app)
        assert "/register" in text
        assert "/login" in text
        assert "/quit" in text


@pytest.mark.anyio
async def test_quit_prints_goodbye_and_exits() -> None:
    app = SmacApp(FakeApi())
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await pilot.press(*"/quit")
        await pilot.press("enter")
        await _wait_until(
            pilot, lambda: any("goodbye" in line for line in app._log_lines)
        )
