"""`smac_cli.app.SmacApp` + `smac_cli.commands`: the TUI shell.

Drives the real `SmacApp` through Textual's own test harness
(`App.run_test()` + `Pilot`) against a hand-rolled `FakeApi` that
implements every `SmacApi` method name the shell calls -- no real server,
no real HTTP. `Path.home` is monkeypatched to `tmp_path` (the same
pattern `test_tui_api.py` uses) so the workspace-name sidecar cache never
touches a developer's real `~/.config/smac`.

Identity v2 (SMAC-79 Task 3, spec §6): `FakeApi` mirrors the NEW
`SmacApi` method set -- `signup`/`login`/`enter_workspace`/
`create_workspace`/`join_public`/`join_code`/`mint_invite_code` -- one
honest fake shared by every test in this module (and re-imported by
`test_tui_commands.py`), not per-test lambdas.

Because every `SmacApi` call runs on a `run_worker(thread=True)` worker
(the whole point of the design -- see `smac_cli/app.py`'s module
docstring), these tests can't just `await pilot.press(...)` and assume
the effect landed: the effect happens on a background OS thread that
gets scheduled independently. `_wait_until` polls (with `pilot.pause`,
which actually yields wall-clock time slices) until the expected UI
state shows up, or times out.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from smac_cli import CLIENT_VERSION
from smac_cli.api import Session
from smac_cli.app import DEFAULT_URL, SmacApp, cache_workspace_name, main, resolve_url
from smac_cli.errors import AuthError, RateLimitedError, SessionExpired, Unreachable
from smac_cli.paths import session_path


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

    Mirrors the real client's Identity v2 contract just closely enough to
    exercise the shell's logic: `signup`/`login` set `self.session` to an
    ACCOUNT-only session (no workspace fields); `enter_workspace`/
    `create_workspace`/`join_public`/`join_code` all fill in the
    workspace fields of that SAME session object, exactly like the real
    `SmacApi` does (never swapping in a brand-new `Session`, so a test's
    `fake.session is not None` checks stay meaningful across a whole
    flow).
    """

    def __init__(
        self, *, session: Session | None = None, server_version: str = CLIENT_VERSION
    ) -> None:
        self.url = "http://fake.example"
        self.session = session
        self.server_version = server_version
        self.meta_error: Exception | None = None
        # /login stubs: the memberships list `POST /accounts/login` would
        # return, or an error (e.g. AuthError for wrong credentials --
        # Identity v2 login is a REAL login, not the old discover-style
        # "always 200, empty list" simulation).
        self.login_result: list[dict[str, Any]] = []
        self.login_error: Exception | None = None
        self.search_result: list[dict[str, Any]] = []
        self.whoami_error: Exception | None = None
        self.posts: list[tuple[str, str]] = []
        self.search_calls: list[str] = []
        self._next_workspace_id = 0
        self.enter_workspace_calls: list[str] = []
        self.enter_workspace_error: Exception | None = None
        self.create_workspace_error: Exception | None = None
        self.join_public_error: Exception | None = None
        self.join_code_error: Exception | None = None
        #: What `/join <code>` resolves the code to -- (workspace_id,
        #: workspace_name); a test overrides this to pick a specific target.
        self.join_code_target: tuple[str, str] = ("ws-code", "Coded Co")
        self.mint_invite_result: dict[str, Any] = {
            "invite_id": "inv-1",
            "code": "shareable-abc123",
            "invite_type": "code",
        }
        self.mint_invite_error: Exception | None = None
        # Live-room stubs (SMAC-72 task 5): a lone "general" channel, no
        # history, mark-read a no-op, no other members. `ws_channel_url`/
        # `ws_events_url` are deliberately NOT implemented -- ChannelFeed/
        # EventBell's reconnect loop treats the resulting `AttributeError`
        # like any other connect failure (see `smac_cli/live.py`'s module
        # docstring), so these shell tests never touch a real socket.
        self.channels_result: list[dict[str, Any]] = [
            {"channel_id": "general-id", "channel_name": "general"}
        ]
        self.messages_result: list[dict[str, Any]] = []
        self.members_result: list[dict[str, Any]] = []
        self.mark_read_calls: list[str] = []
        self.post_error: Exception | None = None
        # /whoami, /channels+/unreads, /channel create, /workspace delete
        # (SMAC-72 task 6) stubs.
        self.whoami_result = {
            "handle": "vraguraman",
            "member_id": "m1",
            "first_name": "Vimal",
            "last_name": "Raguraman",
            "member_name": "Vimal Raguraman",
            "is_admin": True,
            "workspace_visibility": "private",
        }
        self.unreads_result: dict[str, Any] = {"unreads": []}
        self.create_channel_result: dict[str, Any] | None = None
        self.create_channel_error: Exception | None = None
        self.create_channel_calls: list[str] = []
        self.delete_workspace_calls: int = 0
        self.delete_workspace_error: Exception | None = None

    def meta(self) -> dict[str, Any]:
        if self.meta_error is not None:
            raise self.meta_error
        return {"server_version": self.server_version, "api_version": 1}

    # -- account-tier ----------------------------------------------------

    def signup(self, email: str, password: str) -> Session:
        session = Session(
            url=self.url,
            email=email,
            account_access_token="aat",
            account_refresh_token="art",
        )
        self.session = session
        return session

    def login(self, email: str, password: str) -> tuple[Session, list[dict[str, Any]]]:
        if self.login_error is not None:
            raise self.login_error
        session = Session(
            url=self.url,
            email=email,
            account_access_token="aat",
            account_refresh_token="art",
        )
        self.session = session
        return session, self.login_result

    def enter_workspace(self, workspace_id: str) -> None:
        self.enter_workspace_calls.append(workspace_id)
        if self.enter_workspace_error is not None:
            raise self.enter_workspace_error
        assert self.session is not None
        self.session.workspace_id = workspace_id
        self.session.access_token = "at"
        self.session.refresh_token = "rt"

    def _mint_workspace(self, workspace_id: str) -> Session:
        assert self.session is not None
        self.session.workspace_id = workspace_id
        self.session.access_token = "at"
        self.session.refresh_token = "rt"
        return self.session

    def create_workspace(
        self, name: str, visibility: str, first_name: str, last_name: str
    ) -> tuple[Session, str]:
        if self.create_workspace_error is not None:
            raise self.create_workspace_error
        self._next_workspace_id += 1
        session = self._mint_workspace(f"ws-{self._next_workspace_id}")
        return session, name

    def join_public(
        self, workspace_id: str, first_name: str, last_name: str
    ) -> tuple[Session, str]:
        if self.join_public_error is not None:
            raise self.join_public_error
        name = next(
            (
                w["workspace_name"]
                for w in self.search_result
                if w["workspace_id"] == workspace_id
            ),
            workspace_id,
        )
        session = self._mint_workspace(workspace_id)
        return session, name

    def join_code(
        self, code: str, first_name: str, last_name: str
    ) -> tuple[Session, str]:
        if self.join_code_error is not None:
            raise self.join_code_error
        workspace_id, workspace_name = self.join_code_target
        session = self._mint_workspace(workspace_id)
        return session, workspace_name

    def mint_invite_code(self) -> dict[str, Any]:
        if self.mint_invite_error is not None:
            raise self.mint_invite_error
        return self.mint_invite_result

    def search_public(self, q: str = "") -> list[dict[str, str]]:
        self.search_calls.append(q)
        q_lower = q.lower()
        return [w for w in self.search_result if q_lower in w["workspace_name"].lower()]

    # -- workspace-tier ----------------------------------------------------

    def whoami(self) -> dict[str, Any]:
        if self.whoami_error is not None:
            raise self.whoami_error
        return self.whoami_result

    def post(self, channel_id: str, text: str) -> dict[str, Any]:
        self.posts.append((channel_id, text))
        if self.post_error is not None:
            raise self.post_error
        return {}

    def channels(self) -> list[dict[str, Any]]:
        return self.channels_result

    def messages(
        self, channel_id: str, after: str | None = None, limit: int = 15
    ) -> list[dict[str, Any]]:
        return self.messages_result

    def mark_read(self, channel_id: str) -> dict[str, Any]:
        self.mark_read_calls.append(channel_id)
        return {}

    def members(self) -> list[dict[str, Any]]:
        return self.members_result

    def unreads(self) -> dict[str, Any]:
        return self.unreads_result

    def create_channel(self, name: str) -> dict[str, Any]:
        self.create_channel_calls.append(name)
        if self.create_channel_error is not None:
            raise self.create_channel_error
        if self.create_channel_result is not None:
            return self.create_channel_result
        return {"channel_id": f"{name}-id", "channel_name": name}

    def delete_workspace(self) -> dict[str, Any]:
        self.delete_workspace_calls += 1
        if self.delete_workspace_error is not None:
            raise self.delete_workspace_error
        return {"status": "deleted"}


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
        assert "/join" in text
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


def _full_session(**overrides: Any) -> Session:
    """A fully-authenticated session (account + workspace tokens both
    present) -- the common case for tests that restore straight into a
    workspace."""
    defaults: dict[str, Any] = dict(
        url="http://fake.example",
        email="vimal@example.com",
        account_access_token="aat",
        account_refresh_token="art",
        workspace_id="ws-cached",
        access_token="at",
        refresh_token="rt",
    )
    defaults.update(overrides)
    return Session(**defaults)


@pytest.mark.anyio
async def test_session_restore_lands_in_general_with_cached_name() -> None:
    session = _full_session()
    cache_workspace_name("ws-cached", "AI Finance Co")
    app = SmacApp(FakeApi(session=session))
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.header_text == "AI Finance Co — #general")
        assert app.current_channel_name == "general"
        # No welcome banner: a restored valid session skips it entirely.
        assert "Welcome to SMAC" not in _body_text(app)


@pytest.mark.anyio
async def test_session_expired_falls_back_to_welcome_screen() -> None:
    session = _full_session()
    fake = FakeApi(session=session)
    fake.whoami_error = SessionExpired()
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("session expired" in line for line in app._log_lines)
        )
        assert app.header_text == "SMAC — not logged in"
        assert "Welcome to SMAC" in _body_text(app)


@pytest.mark.anyio
async def test_old_session_without_account_tokens_treated_as_expired(
    tmp_path: Path,
) -> None:
    """Backward compat (this task's binding requirement): a session.json
    written by a pre-Identity-v2 build has no `account_access_token` at
    all. It must never crash the restore, and must land on the SAME
    "session expired — /login" message a genuinely-expired session shows
    -- WITHOUT ever calling `whoami()` (there is nothing a request could
    succeed with; every server-side refresh token was purged by the
    Identity v2 migration)."""
    legacy_session = Session(
        url="http://fake.example",
        email="vimal@example.com",
        workspace_id="ws-1",
        access_token="at",
        refresh_token="rt",
        # account_access_token/account_refresh_token default to None --
        # exactly the shape `Session.load` produces for an old file.
    )
    fake = FakeApi(session=legacy_session)

    def _boom() -> dict[str, Any]:
        raise AssertionError("whoami() must not be called for an old-format session")

    fake.whoami = _boom  # type: ignore[method-assign]
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("session expired" in line for line in app._log_lines)
        )
        assert app.header_text == "SMAC — not logged in"
        assert "Welcome to SMAC" in _body_text(app)
        assert app.api.session is None


@pytest.mark.anyio
async def test_account_only_session_restore_shows_no_workspace_state() -> None:
    """A session saved right after `/register` (account tokens, no
    workspace) restores straight into the "no workspace yet" screen --
    never the logged-out welcome screen, and `whoami()` (workspace-tier)
    is never attempted."""
    session = Session(
        url="http://fake.example",
        email="vimal@example.com",
        account_access_token="aat",
        account_refresh_token="art",
    )
    fake = FakeApi(session=session)

    def _boom() -> dict[str, Any]:
        raise AssertionError("whoami() must not be called with no workspace entered")

    fake.whoami = _boom  # type: ignore[method-assign]
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        # Wait for the LAST line `show_no_workspace_state` writes (the
        # server-status line), not just the header flip -- each `write_line`/
        # `set_header` call is applied via a separate `call_from_thread`
        # round trip, so waiting on the header alone can observe the screen
        # only partially drawn.
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        assert app.header_text == "SMAC — no workspace yet"
        text = _body_text(app)
        assert "/workspace create <name>" in text
        assert "/join <code>" in text
        assert "Welcome to SMAC" not in text


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
        # register, workspace, join, invite, login, channel, whoami,
        # channels, unreads, help, quit.
        assert app.pullup.option_count == 11

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
async def test_bare_text_no_workspace_shows_next_steps_line() -> None:
    session = Session(
        url="http://fake.example",
        email="vimal@example.com",
        account_access_token="aat",
        account_refresh_token="art",
    )
    app = SmacApp(FakeApi(session=session))
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.header_text == "SMAC — no workspace yet")
        await pilot.press(*"hello there")
        await pilot.press("enter")
        await _wait_until(
            pilot, lambda: any("no workspace yet" in line for line in app._log_lines)
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
# /register: account-only, then /workspace create
# --------------------------------------------------------------------------


async def _run_register(
    pilot: Any,
    app: SmacApp,
    *,
    email: str = "vimal@example.com",
    password: str = "hunter2-pass",
) -> None:
    await pilot.press("/")
    await _wait_until(pilot, lambda: app.pullup.display)
    await pilot.press(*"register")
    await pilot.press("enter")

    await _wait_until(pilot, lambda: app.footer_input.placeholder == "email")
    await pilot.press(*email)
    await pilot.press("enter")

    await _wait_until(pilot, lambda: app.footer_input.placeholder == "password")
    assert app.footer_input.password is True
    await pilot.press(*password)
    await pilot.press("enter")


async def _run_workspace_create(
    pilot: Any,
    app: SmacApp,
    name: str,
    *,
    first_name: str = "Vimal",
    last_name: str = "Raguraman",
    visibility: str | None = None,
) -> None:
    await pilot.press(*f"/workspace create {name}")
    await pilot.press("enter")

    await _wait_until(pilot, lambda: app.footer_input.placeholder == "first name")
    await pilot.press(*first_name)
    await pilot.press("enter")

    await _wait_until(pilot, lambda: app.footer_input.placeholder == "last name")
    await pilot.press(*last_name)
    await pilot.press("enter")

    await _wait_until(pilot, lambda: "visibility" in app.footer_input.placeholder)
    if visibility is not None:
        await pilot.press(*visibility)
    await pilot.press("enter")


@pytest.mark.anyio
async def test_register_lands_in_no_workspace_state() -> None:
    fake = FakeApi()
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await _run_register(pilot, app)

        await _wait_until(pilot, lambda: app.header_text == "SMAC — no workspace yet")
        text = _body_text(app)
        assert "account created: vimal@example.com" in text
        assert "/workspace create <name>" in text
        assert "/join <code>" in text
        assert "/login" in text
        assert fake.session is not None
        assert fake.session.account_access_token == "aat"
        assert fake.session.workspace_id is None


@pytest.mark.anyio
async def test_register_then_workspace_create_lands_in_general() -> None:
    fake = FakeApi()
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await _run_register(pilot, app)
        await _wait_until(pilot, lambda: app.header_text == "SMAC — no workspace yet")

        await _run_workspace_create(pilot, app, "AI Finance Co")

        # Wait on the LAST thing `cmd_workspace`'s create branch writes
        # (the "founded" banner, which lands via its own `call_from_thread`
        # round trip AFTER `enter_workspace`'s header-setting one) rather
        # than the header alone -- polling can otherwise observe the
        # header already flipped while that trailing line hasn't landed
        # yet.
        await _wait_until(
            pilot, lambda: 'workspace "AI Finance Co" founded' in _body_text(app)
        )
        assert app.header_text == "AI Finance Co — #general"
        assert app.current_channel_name == "general"
        assert fake.session is not None
        assert fake.session.workspace_id is not None


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


@pytest.mark.anyio
async def test_workspace_create_escape_cancels_stays_in_no_workspace_state() -> None:
    fake = FakeApi()
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await _run_register(pilot, app)
        await _wait_until(pilot, lambda: app.header_text == "SMAC — no workspace yet")

        await pilot.press(*"/workspace create AI Finance Co")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "first name")

        await pilot.press("escape")
        await _wait_until(pilot, lambda: not app.footer_input.password)
        assert app.header_text == "SMAC — no workspace yet"
        assert fake.session is not None
        assert fake.session.workspace_id is None


# --------------------------------------------------------------------------
# /join <code>
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_join_code_lands_in_general() -> None:
    fake = FakeApi()
    fake.join_code_target = ("ws-code", "Coded Co")
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await _run_register(pilot, app)
        await _wait_until(pilot, lambda: app.header_text == "SMAC — no workspace yet")

        await pilot.press(*"/join abc123")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "first name")
        await pilot.press(*"New")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.footer_input.placeholder == "last name")
        await pilot.press(*"Member")
        await pilot.press("enter")

        # Wait on the trailing "joined" line (see the analogous comment in
        # `test_register_then_workspace_create_lands_in_general`) rather
        # than the header alone.
        await _wait_until(pilot, lambda: 'joined "Coded Co"' in _body_text(app))
        assert app.header_text == "Coded Co — #general"
        assert fake.session is not None
        assert fake.session.workspace_id == "ws-code"


@pytest.mark.anyio
async def test_join_no_code_shows_usage() -> None:
    fake = FakeApi()
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await _run_register(pilot, app)
        await _wait_until(pilot, lambda: app.header_text == "SMAC — no workspace yet")

        await pilot.press(*"/join")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: "usage: /join <code>" in _body_text(app))


@pytest.mark.anyio
async def test_join_logged_out_fails_before_prompting() -> None:
    """Final-review MINOR-4: logged out, `/join <code>` used to ask for
    first name then last name and only THEN die with "No active
    session." -- two wasted answers before an unhelpful message. The
    session check now runs before any prompting, so the actionable
    system line appears immediately and the name prompts never show."""
    app = SmacApp(FakeApi())
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await pilot.press(*"/join abc123")
        await pilot.press("enter")
        await _wait_until(
            pilot,
            lambda: "create an account first: /register (then /join <code>)"
            in _body_text(app),
        )
        assert app.footer_input.placeholder != "first name"


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
async def test_login_one_membership_enters_workspace_directly() -> None:
    fake = FakeApi()
    fake.login_result = [
        {
            "workspace_id": "ws-1",
            "workspace_name": "AI Finance Co",
            "member_id": "m1",
            "handle": "vraguraman",
        }
    ]
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await _start_login(pilot, app, "vimal@example.com", "pw")
        await _wait_until(pilot, lambda: app.header_text == "AI Finance Co — #general")
        assert fake.session is not None
        assert fake.session.workspace_id == "ws-1"
        assert fake.enter_workspace_calls == ["ws-1"]


@pytest.mark.anyio
async def test_login_multi_membership_shows_picker_with_both_names() -> None:
    fake = FakeApi()
    fake.login_result = [
        {
            "workspace_id": "ws-1",
            "workspace_name": "AI Finance Co",
            "member_id": "m1",
            "handle": "vraguraman",
        },
        {
            "workspace_id": "ws-2",
            "workspace_name": "Research Lab",
            "member_id": "m2",
            "handle": "vraguraman2",
        },
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
        assert "your workspaces:" in app._log_lines
        assert "── your workspaces: ──" not in app._log_lines

        # Select the second entry and confirm it enters THAT workspace.
        await pilot.press("down")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app.header_text == "Research Lab — #general")
        assert fake.session is not None
        assert fake.session.workspace_id == "ws-2"
        assert fake.enter_workspace_calls == ["ws-2"]


@pytest.mark.anyio
async def test_login_multi_membership_escape_cancels_to_no_workspace_state() -> None:
    fake = FakeApi()
    fake.login_result = [
        {
            "workspace_id": "ws-1",
            "workspace_name": "AI Finance Co",
            "member_id": "m1",
            "handle": "vraguraman",
        },
        {
            "workspace_id": "ws-2",
            "workspace_name": "Research Lab",
            "member_id": "m2",
            "handle": "vraguraman2",
        },
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
        # The account login itself already happened (real, not simulated)
        # -- cancelling the workspace picker lands back on the
        # "no workspace yet" state, not a logged-out one.
        await _wait_until(pilot, lambda: app.header_text == "SMAC — no workspace yet")
        assert fake.session is not None
        assert fake.session.workspace_id is None
        assert not app.pullup.display


@pytest.mark.anyio
async def test_login_zero_memberships_join_flow_filters_and_registers() -> None:
    fake = FakeApi()
    fake.login_result = []
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
        assert "public workspaces (type to search):" in app._log_lines
        assert (
            "(or /workspace create <name>, or /join <code>, or Esc to go back)"
            in app._log_lines
        )
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
async def test_login_zero_memberships_join_frame_escape_cancels_to_no_workspace_state() -> (
    None
):
    fake = FakeApi()
    fake.login_result = []
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
        await _wait_until(pilot, lambda: app.header_text == "SMAC — no workspace yet")
        assert fake.session is not None
        assert fake.session.workspace_id is None
        assert not app.pullup.display


@pytest.mark.anyio
async def test_login_wrong_credentials_shows_server_message_directly() -> None:
    """Unlike the retired discover-based flow, `POST /accounts/login` is a
    REAL login -- wrong credentials raise `AuthError` straight out of
    `api.login`, propagating to `SmacApp._run_command`'s generic
    message-only handling. There's no more "zero matches" ambiguity
    between an unknown email and a mistyped password to soften with a
    hint (spec: that whole class of finding no longer exists); "zero
    matches" from a SUCCESSFUL login now only ever means "this account
    has no workspace yet" (covered above)."""
    fake = FakeApi()
    fake.login_error = AuthError("invalid_credentials", "Invalid email or password")
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        await _start_login(pilot, app, "vimal@example.com", "wrong-password")
        await _wait_until(pilot, lambda: "Invalid email or password" in _body_text(app))
        assert app.header_text == "SMAC — not logged in"
        assert fake.session is None


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
        assert "/join" in text
        assert "/invite" in text
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


# --------------------------------------------------------------------------
# post_current: 429 preserves the draft (SMAC-72 task 5)
#
# Exercised against `FakeApi` (deterministic, instant) rather than a real
# server: `tests/conftest.py` sets `RATE_LIMIT_POSTS=1000` for the whole
# suite (so the many message-heavy tests elsewhere don't trip it), which
# makes actually exhausting the real limiter within a real-server test
# impractically slow -- `post_current`'s error-handling logic itself is
# what's under test here, not the server's rate limiter.
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rate_limited_send_preserves_draft_and_shows_server_message() -> None:
    fake = FakeApi(session=_full_session())
    fake.post_error = RateLimitedError(
        "rate_limited", "Posting too fast — wait a moment"
    )
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)

        await pilot.press(*"hello there")
        await pilot.press("enter")

        await _wait_until(
            pilot, lambda: any("too fast" in line for line in app._log_lines)
        )
        assert fake.posts == [("general-id", "hello there")]
        # The draft is restored into the input, never silently lost.
        assert app.footer_input.value == "hello there"


@pytest.mark.anyio
async def test_non_rate_limit_error_does_not_restore_draft() -> None:
    from smac_cli.errors import NotAMemberError

    fake = FakeApi(session=_full_session())
    fake.post_error = NotAMemberError(
        "not_a_member", "You are not a member of this channel"
    )
    app = SmacApp(fake)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)

        await pilot.press(*"hello there")
        await pilot.press("enter")

        await _wait_until(
            pilot, lambda: any("not a member" in line for line in app._log_lines)
        )
        # Only a 429 preserves the draft -- any other error just reports.
        assert app.footer_input.value == ""


# --------------------------------------------------------------------------
# `resolve_url` / `main`: SMAC_URL env var + --url flag precedence
# (finding A -- `smac-server --port 9000` used to be permanently
# unreachable from `smac`: no flag, no env var, and a session file only
# ever gets a URL from a login that couldn't happen against a non-default
# port in the first place.)
# --------------------------------------------------------------------------


def test_resolve_url_defaults_when_nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMAC_URL", raising=False)
    assert resolve_url([]) == DEFAULT_URL


def test_resolve_url_env_var_wins_over_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMAC_URL", "http://env.example:9000")
    assert resolve_url([]) == "http://env.example:9000"


def test_resolve_url_flag_wins_over_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMAC_URL", "http://env.example:9000")
    assert (
        resolve_url(["--url", "http://flag.example:9001"]) == "http://flag.example:9001"
    )


def test_resolve_url_flag_wins_over_default_with_no_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMAC_URL", raising=False)
    assert (
        resolve_url(["--url", "http://flag.example:9001"]) == "http://flag.example:9001"
    )


def test_main_uses_session_url_ignoring_flag_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restored session's own URL wins over BOTH `--url` and `SMAC_URL`
    -- it holds the tokens for the specific server it logged into, so
    letting an ambient env var or flag redirect it would silently send
    those tokens to a different server."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    session = _full_session(url="http://session.example:8000", workspace_id="w1")
    session.save(session_path())
    monkeypatch.setenv("SMAC_URL", "http://env.example:9000")

    captured: dict[str, str] = {}

    def fake_run(self: SmacApp) -> None:
        captured["url"] = self.api.url

    monkeypatch.setattr(SmacApp, "run", fake_run)

    main(["--url", "http://flag.example:9001"])

    assert captured["url"] == "http://session.example:8000"


def test_main_uses_flag_over_env_when_no_session_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SMAC_URL", "http://env.example:9000")

    captured: dict[str, str] = {}

    def fake_run(self: SmacApp) -> None:
        captured["url"] = self.api.url

    monkeypatch.setattr(SmacApp, "run", fake_run)

    main(["--url", "http://flag.example:9001"])

    assert captured["url"] == "http://flag.example:9001"


def test_main_uses_env_over_default_when_no_session_or_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SMAC_URL", "http://env.example:9000")

    captured: dict[str, str] = {}

    def fake_run(self: SmacApp) -> None:
        captured["url"] = self.api.url

    monkeypatch.setattr(SmacApp, "run", fake_run)

    main([])

    assert captured["url"] == "http://env.example:9000"


# --------------------------------------------------------------------------
# _call_ui: post-shutdown fallback drops the update instead of mutating
# widgets from a worker thread (finding I).
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_call_ui_drops_update_after_shutdown_instead_of_mutating() -> None:
    """`run_test()`'s own teardown does NOT reset `_loop`/`_thread_id` the
    way `App.run()`'s real production shutdown does (`app._loop = None`,
    `app._thread_id = 0`, set at the very end of Textual's `run_async`) --
    so those are set explicitly here to reproduce a genuinely closed loop,
    the state a straggling background feed/bell thread's callback
    (`_deliver_from_feed_thread`) can actually hit post-shutdown. The call
    is made from a REAL different OS thread, same as any such callback
    always is -- never the app's own thread."""
    app = SmacApp(FakeApi())
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
    app._loop = None
    app._thread_id = 0

    called = {"ran": False}
    error: list[BaseException] = []

    def fn() -> None:
        called["ran"] = True

    def call_from_worker() -> None:
        try:
            app._call_ui(fn)
        except BaseException as exc:  # pragma: no cover - surfaced via `error`
            error.append(exc)

    worker = threading.Thread(target=call_from_worker)
    worker.start()
    worker.join(timeout=5.0)

    assert not worker.is_alive()
    assert error == []
    assert called["ran"] is False


@pytest.mark.anyio
async def test_call_ui_still_runs_fn_directly_from_the_app_thread() -> None:
    """The legitimate same-thread fallback (e.g. `action_clean_quit`
    calling `system_line` directly on the event loop, never via
    `call_from_thread`) must keep working -- only the post-shutdown case
    changed. `run_test()` runs the app on the same OS thread as the test
    coroutine, so this call is exactly the "already on the app thread"
    case `_call_ui` special-cases."""
    app = SmacApp(FakeApi())
    async with app.run_test() as pilot:
        await _wait_until(
            pilot, lambda: any("server:" in line for line in app._log_lines)
        )
        called = {"ran": False}

        def fn() -> None:
            called["ran"] = True

        app._call_ui(fn)

        assert called["ran"] is True
