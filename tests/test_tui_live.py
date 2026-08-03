"""The live room (SMAC-72 task 5): channel feed, scrolling/follow-state,
the mention bell, load-older pagination, and reconnect.

Two layers:

1. `smac_cli.live`'s reconnect mechanics in isolation, against a tiny
   local `websockets.sync.server` echo/drop server -- fast, deterministic,
   no `smac-server` needed just to prove backoff/reconnect/`stop()` work.
2. The full `SmacApp` live room against a REAL spawned `smac-server`
   (`real_smac_server`, the module-scoped fixture `tests/conftest.py`
   already shares with `test_tui_api.py` -- reused here rather than
   spawning a second server), driven through Textual's own
   `App.run_test()`/`Pilot`. A second identity -- an agent minted via
   `POST /members/agents` and posted-as via a raw `httpx` call with its
   `X-API-Key` (`SmacApi` itself has no agent-auth support, and doesn't
   need any for the TUI's own purposes) -- plays "the other member" for
   the mention/live-feed-from-someone-else scenarios.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from smac_cli.api import SmacApi
from smac_cli.app import SmacApp
from smac_cli.live import ChannelFeed

_TEST_PASSWORD = "test-password-123"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _wait_until(pilot: Any, predicate: Any, *, timeout: float = 10.0) -> None:
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
    """Call `post_fn()` (posting as "the other member") repeatedly until a
    line containing `contains` shows up in `app._log_lines`.

    A message posted the instant after `enter_channel` starts the channel
    feed/mention-bell threads can land before that background thread's
    WebSocket handshake actually completes -- the server only pushes to
    sockets that are ALREADY connected (`app/ws_manager.py`, no queueing
    for a late joiner), so a single post right after `current_channel_id`
    becomes non-`None` can be silently missed. Re-posting is harmless for
    an "eventually shows up" assertion; a true failure to wire the
    feed/bell still fails loudly after `attempts` tries.
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


# --------------------------------------------------------------------------
# Test-only helpers: found a workspace, mint + post-as an agent.
#
# `SmacApi` has no agent-auth or channel-membership-management support (the
# TUI never needs either), so these speak `httpx` directly -- exactly the
# same shape the brief's own suggestion ("agents via POST /members/agents
# for mention scenarios") points at.
# --------------------------------------------------------------------------


def _found_workspace(url: str) -> SmacApi:
    """Found a brand-new private workspace + admin human account."""
    api = SmacApi(url)
    api.register_found(
        email=f"{_unique('founder')}@test.example",
        password=_TEST_PASSWORD,
        first_name="Ada",
        last_name="Lovelace",
        workspace_name=_unique("wksp"),
        visibility="private",
    )
    return api


def _general_channel_id(api: SmacApi) -> str:
    match = next(c for c in api.channels() if c["channel_name"].lower() == "general")
    return str(match["channel_id"])


def _create_channel(api: SmacApi, name: str) -> dict[str, Any]:
    return api.create_channel(name)


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


def _app_api_for(founder: SmacApi) -> SmacApi:
    """A second `SmacApi` (its own httpx client) sharing the founder's
    session -- what `SmacApp` itself drives with, so the founder's own
    `SmacApi` instance stays free for test-setup/assertion calls without
    racing the app's internal token refreshes."""
    assert founder.session is not None
    return SmacApi(founder.url, session=replace(founder.session))


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


# --------------------------------------------------------------------------
# `smac_cli.live` in isolation: a local echo/drop WebSocket server.
# --------------------------------------------------------------------------


def test_channel_feed_reconnects_after_the_socket_drops() -> None:
    """One local server: its handler closes the FIRST connection
    immediately (simulating a drop) and keeps the second one open. No
    server restart/port-rebind needed -- `ChannelFeed` reconnecting to
    the very same still-listening server is exactly what a real drop
    (the server hiccups, or a proxy resets the socket) looks like from
    the client's side.
    """
    from websockets.sync.server import serve

    connections = 0

    def handler(ws: Any) -> None:
        nonlocal connections
        connections += 1
        if connections == 1:
            return  # close immediately: connection #1 "drops"
        for _raw in ws:  # connection #2+: stay open, idle
            pass

    server = serve(handler, "127.0.0.1", 0)
    host, port = server.socket.getsockname()
    import threading

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        events: list[dict[str, Any]] = []
        feed = ChannelFeed(lambda: f"ws://{host}:{port}", events.append)
        feed.start()
        try:
            deadline = time.monotonic() + 10.0
            while connections < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
            assert connections >= 2, "feed never reconnected after the drop"

            deadline = time.monotonic() + 5.0
            while (
                not any(e.get("event") == "reconnected" for e in events)
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)

            assert any(e.get("event") == "disconnected" for e in events)
            assert any(e.get("event") == "reconnected" for e in events)
        finally:
            feed.stop()
    finally:
        server.shutdown()
        server_thread.join(timeout=5.0)


def test_channel_feed_stop_is_prompt_and_thread_is_daemon() -> None:
    from websockets.sync.server import serve

    def handler(ws: Any) -> None:
        for _raw in ws:
            pass

    server = serve(handler, "127.0.0.1", 0)
    host, port = server.socket.getsockname()
    import threading

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        feed = ChannelFeed(lambda: f"ws://{host}:{port}", lambda p: None)
        feed.start()
        assert feed._thread.daemon is True
        time.sleep(0.2)  # let it connect
        started = time.monotonic()
        feed.stop()
        # stop() should not block minutes waiting on a recv() poll tick.
        assert time.monotonic() - started < 2.0
    finally:
        server.shutdown()
        server_thread.join(timeout=5.0)


# --------------------------------------------------------------------------
# The full live room against a real server.
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_posted_message_appears_in_feed_including_self_echo(
    real_smac_server: tuple[str, Path],
) -> None:
    url, _home_dir = real_smac_server
    founder = _found_workspace(url)
    app = SmacApp(_app_api_for(founder))

    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)

        await pilot.press(*"hello from the human")
        await pilot.press("enter")

        await _wait_until(pilot, lambda: "hello from the human" in _body_text(app))
        assert any(line.endswith("hello from the human") for line in app._log_lines)


@pytest.mark.anyio
async def test_message_from_another_member_appears_with_rendered_mention(
    real_smac_server: tuple[str, Path],
) -> None:
    url, _home_dir = real_smac_server
    founder = _found_workspace(url)
    assert founder.session is not None
    workspace_id = founder.session.workspace_id
    channel_id = _general_channel_id(founder)
    whoami = founder.whoami()

    agent = _register_agent(url, workspace_id, founder.session.access_token, "analyst")
    _add_channel_member(
        url, workspace_id, founder.session.access_token, channel_id, agent["member_id"]
    )

    app = SmacApp(_app_api_for(founder))
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)

        await _post_until_seen(
            pilot,
            lambda: _agent_post(
                url,
                workspace_id,
                channel_id,
                agent["api_key"],
                f"@{whoami['handle']} the numbers are in",
            ),
            "the numbers are in",
            app,
        )
        line = next(l for l in app._log_lines if "the numbers are in" in l)
        assert line.startswith("[")
        assert "analyst:" in line
        assert f"@{whoami['handle']}" in line
        assert "<@" not in line  # the raw token never leaks through


@pytest.mark.anyio
async def test_switching_channel_loads_history_and_marks_read(
    real_smac_server: tuple[str, Path],
) -> None:
    url, _home_dir = real_smac_server
    founder = _found_workspace(url)
    assert founder.session is not None
    workspace_id = founder.session.workspace_id
    bearer = founder.session.access_token

    other = _create_channel(founder, _unique("reports"))
    other_id = other["channel_id"]
    whoami = founder.whoami()
    _add_channel_member(url, workspace_id, bearer, other_id, whoami["member_id"])

    agent = _register_agent(url, workspace_id, bearer, "risk-bot")
    _add_channel_member(url, workspace_id, bearer, other_id, agent["member_id"])
    _agent_post(url, workspace_id, other_id, agent["api_key"], "waiting for you")

    unreads_before = founder.unreads()
    row_before = next(
        r for r in unreads_before["unreads"] if r["channel_id"] == other_id
    )
    assert row_before["unread_count"] > 0

    app = SmacApp(_app_api_for(founder))
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)

        await pilot.press(*f"/channel {other['channel_name']}")
        await pilot.press("enter")

        await _wait_until(
            pilot, lambda: app.current_channel_name == other["channel_name"]
        )
        assert app.header_text.endswith(f"#{other['channel_name']}")
        await _wait_until(
            pilot, lambda: any("waiting for you" in line for line in app._log_lines)
        )

        def _caught_up() -> bool:
            row = next(
                r for r in founder.unreads()["unreads"] if r["channel_id"] == other_id
            )
            return bool(row["unread_count"] == 0)

        await _wait_until(pilot, _caught_up)


@pytest.mark.anyio
async def test_mention_in_other_channel_shows_bell_line(
    real_smac_server: tuple[str, Path],
) -> None:
    url, _home_dir = real_smac_server
    founder = _found_workspace(url)
    assert founder.session is not None
    workspace_id = founder.session.workspace_id
    bearer = founder.session.access_token
    whoami = founder.whoami()

    reports = _create_channel(founder, _unique("reports"))
    agent = _register_agent(url, workspace_id, bearer, "risk-bot")
    _add_channel_member(
        url, workspace_id, bearer, reports["channel_id"], whoami["member_id"]
    )
    _add_channel_member(
        url, workspace_id, bearer, reports["channel_id"], agent["member_id"]
    )

    app = SmacApp(_app_api_for(founder))
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        # Still in #general when the mention lands in #reports.
        assert app.current_channel_name == "general"

        await _post_until_seen(
            pilot,
            lambda: _agent_post(
                url,
                workspace_id,
                reports["channel_id"],
                agent["api_key"],
                f"@{whoami['handle']} exposure above threshold",
            ),
            "you were mentioned",
            app,
        )
        line = next(l for l in app._log_lines if "you were mentioned" in l)
        assert f"#{reports['channel_name']}" in line
        assert "@risk-bot" in line


@pytest.mark.anyio
async def test_mention_in_current_channel_does_not_ring_bell(
    real_smac_server: tuple[str, Path],
) -> None:
    url, _home_dir = real_smac_server
    founder = _found_workspace(url)
    assert founder.session is not None
    workspace_id = founder.session.workspace_id
    bearer = founder.session.access_token
    channel_id = _general_channel_id(founder)
    whoami = founder.whoami()

    agent = _register_agent(url, workspace_id, bearer, "helper-bot")
    _add_channel_member(url, workspace_id, bearer, channel_id, agent["member_id"])

    app = SmacApp(_app_api_for(founder))
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)

        await _post_until_seen(
            pilot,
            lambda: _agent_post(
                url,
                workspace_id,
                channel_id,
                agent["api_key"],
                f"@{whoami['handle']} you around?",
            ),
            "you around?",
            app,
        )
        # The mention is already visible as an ordinary message line -- no
        # redundant bell for the channel currently open.
        assert not any("you were mentioned" in line for line in app._log_lines)


@pytest.mark.anyio
async def test_pgup_pauses_follow_and_shows_live_new_count(
    real_smac_server: tuple[str, Path],
) -> None:
    url, _home_dir = real_smac_server
    founder = _found_workspace(url)
    assert founder.session is not None
    workspace_id = founder.session.workspace_id
    bearer = founder.session.access_token
    channel_id = _general_channel_id(founder)

    agent = _register_agent(url, workspace_id, bearer, "filler")
    _add_channel_member(url, workspace_id, bearer, channel_id, agent["member_id"])
    # 30 (a multiple of the server's 15-message page cap) so the "recent
    # history on entry" walk keeps a FULL last page of 15 -- with fewer,
    # the kept page could be much shorter (e.g. 20 messages keeps only the
    # last 5). Long lines (wrapped at 80 columns) so those 15 reliably
    # overflow a 24-row terminal regardless of exact body height -- a
    # short one-line-per-message page can easily fit on screen with
    # nothing to scroll, which would make PageUp a no-op.
    total_filler = 30
    for i in range(total_filler):
        _agent_post(
            url,
            workspace_id,
            channel_id,
            agent["api_key"],
            f"filler line {i} " + "x" * 200,
        )

    app = SmacApp(_app_api_for(founder))
    async with app.run_test(size=(80, 24)) as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)
        await _wait_until(
            pilot,
            lambda: any(
                f"filler line {total_filler - 1}" in line for line in app._log_lines
            ),
        )
        # Let the RichLog's own auto-scroll actually settle at the bottom
        # before scrolling away from it -- `write()` schedules its
        # `scroll_end()` rather than applying it synchronously, so without
        # this, PageUp could fire while `scroll_y` is still mid-transition
        # (or, worse, hasn't moved from 0 yet) and be a silent no-op.
        await _wait_until(pilot, lambda: app.body.is_vertical_scroll_end)
        assert app._following is True

        await pilot.press("pageup")
        await _wait_until(pilot, lambda: app._following is False)
        assert app.history_indicator.display is True

        _agent_post(
            url, workspace_id, channel_id, agent["api_key"], "one more while paused"
        )

        await _wait_until(pilot, lambda: app._new_since_pause == 1)
        # `Static` doesn't expose its current content publicly; the
        # name-mangled attribute `update()` stores it in is the simplest
        # way to assert on the indicator's actual rendered text.
        indicator_text = str(getattr(app.history_indicator, "_Static__content"))
        assert "1 new below" in indicator_text
        # Paused: the view must not have been yanked back to the bottom.
        assert app.body.is_vertical_scroll_end is False

        await pilot.press("end")
        await _wait_until(pilot, lambda: app._following is True)
        assert app.history_indicator.display is False

        def _marked_read() -> bool:
            row = next(
                r for r in founder.unreads()["unreads"] if r["channel_id"] == channel_id
            )
            return bool(row["unread_count"] == 0)

        await _wait_until(pilot, _marked_read)


@pytest.mark.anyio
async def test_load_older_history_prepends_without_duplicates(
    real_smac_server: tuple[str, Path],
) -> None:
    url, _home_dir = real_smac_server
    founder = _found_workspace(url)
    assert founder.session is not None
    workspace_id = founder.session.workspace_id
    bearer = founder.session.access_token

    channel = _create_channel(founder, _unique("archive"))
    channel_id = channel["channel_id"]
    whoami = founder.whoami()
    _add_channel_member(url, workspace_id, bearer, channel_id, whoami["member_id"])
    agent = _register_agent(url, workspace_id, bearer, "scribe")
    _add_channel_member(url, workspace_id, bearer, channel_id, agent["member_id"])

    total_messages = 24
    for i in range(total_messages):
        _agent_post(url, workspace_id, channel_id, agent["api_key"], f"msg-{i:02d}")

    app = SmacApp(_app_api_for(founder))
    async with app.run_test(size=(80, 24)) as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)

        await pilot.press(*f"/channel {channel['channel_name']}")
        await pilot.press("enter")
        await _wait_until(
            pilot, lambda: app.current_channel_name == channel["channel_name"]
        )
        await _wait_until(
            pilot, lambda: any("msg-23" in line for line in app._log_lines)
        )
        # "Recent history on entry": only the tail is shown, not everything.
        assert not any("msg-00" in line for line in app._log_lines)
        lines_before = len(app._log_lines)

        # Drive the same mechanism `_on_feed_scroll_changed` calls when the
        # top of the feed is reached, directly -- with only the recent
        # ~9-15-line page loaded, a 24-row test terminal may not actually
        # overflow (nothing to physically scroll), which would make a
        # genuine scroll-to-top gesture geometry-dependent and flaky. The
        # PageUp/follow-state test above already exercises the scroll ->
        # `_on_feed_scroll_changed` wiring with real overflow; this test's
        # job is the pagination itself (correct page, no duplicates).
        app._load_older_history()
        await _wait_until(pilot, lambda: not app._loading_older, timeout=10.0)

        assert any("msg-00" in line for line in app._log_lines)
        # No duplicates anywhere, and still exactly one line per message.
        message_lines = [l for l in app._log_lines if "msg-" in l]
        assert len(message_lines) == len(set(message_lines)) == total_messages
        assert len(app._log_lines) > lines_before

        # Reaching the true beginning of the channel: a further load-older
        # finds nothing before `msg-00` and is a harmless no-op.
        app._load_older_history()
        await _wait_until(pilot, lambda: not app._loading_older, timeout=10.0)
        assert app._history_exhausted is True
        assert len(app._log_lines) == len(message_lines)


@pytest.mark.anyio
async def test_server_restart_triggers_reconnect_and_history_refresh(
    real_smac_server: tuple[str, Path],
) -> None:
    """The last test in this module: it stops/restarts the SHARED server
    process in place (same port, same DB -- `smac_cli.server`'s own
    `--stop`/`--start`), never spawning a second one. Placed last so no
    other test in this module observes the brief transient downtime.
    """
    import os
    import subprocess
    import sys

    url, home_dir = real_smac_server
    founder = _found_workspace(url)
    assert founder.session is not None
    port = url.rsplit(":", 1)[1]

    app = SmacApp(_app_api_for(founder))
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: app.current_channel_id is not None)

        repo_root = Path(__file__).resolve().parents[1]
        env = {**os.environ, "HOME": str(home_dir)}
        stop = subprocess.run(
            [sys.executable, "-m", "smac_cli.server", "--stop"],
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert stop.returncode == 0, stop.stdout + stop.stderr

        await _wait_until(
            pilot,
            lambda: any("disconnected" in line for line in app._log_lines),
            timeout=15.0,
        )

        start = subprocess.run(
            [sys.executable, "-m", "smac_cli.server", "--start", "--port", port],
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert start.returncode == 0, start.stdout + start.stderr

        await _wait_until(
            pilot,
            lambda: any("channel reconnected" in line for line in app._log_lines),
            timeout=20.0,
        )
