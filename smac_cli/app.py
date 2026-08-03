"""The `smac` TUI client: layout, the footer input contract, the pull-up
command list, and the auth flows (spec `docs/superpowers/specs/
2026-08-03-smac-tui-design.md`, §2 + §0.1 Frames 1-4/3b/3c).

Three regions (`SmacApp.compose`): a one-line header (`<workspace> —
#<channel>`, or "SMAC — not logged in"), a scrollable `RichLog` body (the
message feed + dim system lines), and a footer `FooterInput` -- the ONE
input bar the whole app reads from. Typing `/` raises a pull-up
(`OptionList`, mounted directly above the input so it visually appears
above the bar) that filters live; anything not starting with `/` is a
send.

Every `SmacApi` call is BLOCKING, so every command handler
(`smac_cli.commands.COMMANDS`) runs on a `run_worker(thread=True)` worker
(`_run_command`) -- never on the event loop. The two inline-form helpers
handlers use, `ask()` and `choose()`, are built the same way: they hand a
`threading.Event` to the worker thread, ask the main thread (via
`call_from_thread`) to put the UI into "waiting for an answer" mode, and
block the *worker* thread (never the event loop) until `Input.Submitted`
or Escape resolves that event. This is why `ask()`/`choose()` may only be
called from a worker thread -- calling them from the event loop itself
would raise (Textual's own `call_from_thread` guards against exactly
that).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from smac_cli import CLIENT_VERSION
from smac_cli.api import DEFAULT_MESSAGE_LIMIT, Session, SmacApi
from smac_cli.commands import COMMANDS
from smac_cli.errors import (
    RateLimitedError,
    SessionExpired,
    SmacError,
    Unreachable,
)
from smac_cli.live import ChannelFeed, EventBell
from smac_cli.paths import config_dir, session_path
from smac_cli.render import bell_line, message_line

#: Where `smac` talks by default when no saved session says otherwise --
#: matches `smac_cli.server`'s own default port (spec Frame 1).
DEFAULT_URL = "http://127.0.0.1:8000"

_WORKSPACE_NAME_CACHE_FILE = "workspace_name_cache.json"

_IDLE_PLACEHOLDER = "/register, /login, or type a message"


class FormCancelled(Exception):
    """Raised by `ask()`/`choose()` when Esc cancels the whole form.

    Command handlers don't need to catch this themselves -- letting it
    propagate out of the handler is exactly the "Esc cancels the whole
    flow" contract; `SmacApp._run_command` catches it once, for every
    command, in one place.
    """


def _workspace_name_cache_path() -> Path:
    """`~/.config/smac/workspace_name_cache.json` -- workspace_id -> name.

    `SmacApi.Session` (spec-pinned shape) doesn't carry `workspace_name`,
    only `workspace_id` -- so a restored session (Frame 8, "every later
    launch") has no name to put in the header until *something* remembers
    it. This tiny sidecar (written whenever a name is learned via
    register/login/join) is that memory; it's app-local bookkeeping, not
    part of the officially pinned session file.
    """
    return config_dir() / _WORKSPACE_NAME_CACHE_FILE


def cache_workspace_name(workspace_id: str, name: str) -> None:
    """Remember `name` for `workspace_id`, merging into the existing cache."""
    path = _workspace_name_cache_path()
    data: dict[str, str] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data[workspace_id] = name
    path.write_text(json.dumps(data), encoding="utf-8")


def cached_workspace_name(workspace_id: str) -> str | None:
    """The last name cached for `workspace_id`, or `None` if unknown."""
    path = _workspace_name_cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get(workspace_id)
    return value if isinstance(value, str) else None


def _walk_message_pages(
    api: SmacApi,
    channel_id: str,
    *,
    stop_before_id: str | None = None,
    page_size: int = DEFAULT_MESSAGE_LIMIT,
) -> list[list[dict[str, Any]]]:
    """Walk `channel_id`'s history forward from the very beginning, in
    pages of `page_size`, returning every page fetched along the way.

    **Why walking, not a single call:** `GET .../messages` (`app/routers/
    messages.py`) only supports a forward-anchored `after` cursor --
    "everything with a higher `seq` than this message", capped server-side
    at `MAX_LIMIT` (15) messages *per call no matter what limit is
    requested*. There is no `before`/descending option, so "the most
    recent page" and "the page immediately before some anchor" both have
    to be produced by walking forward from position 0 and keeping only
    the page that matters:

    - **Recent history on entry** (`SmacApp.enter_channel`): walk with no
      `stop_before_id`, keep `pages[-1]` -- the walk stops the moment a
      short page (fewer than `page_size` messages) is returned, which is
      exactly "caught up to now", so this is the tail end of the channel,
      not its beginning.
    - **Load-older** (`SmacApp._load_older_history`): walk with
      `stop_before_id` set to the oldest message currently shown, keep
      `pages[-1]` -- the page immediately preceding that anchor, with the
      anchor itself excluded (never duplicated).

    Both cases cost O(total messages so far / page_size) requests, which
    is the correct minimal approach against an API with no reverse
    cursor: each walk stops as soon as it reaches what it's looking for
    (the tail, or the target anchor), never re-reading the same range
    twice within one call.
    """
    pages: list[list[dict[str, Any]]] = []
    after: str | None = None
    while True:
        page = api.messages(channel_id, after=after, limit=page_size)
        if not page:
            break
        if stop_before_id is not None:
            anchor_index = next(
                (
                    i
                    for i, m in enumerate(page)
                    if m["Message"]["message_id"] == stop_before_id
                ),
                None,
            )
            if anchor_index is not None:
                if anchor_index > 0:
                    pages.append(page[:anchor_index])
                break
        pages.append(page)
        after = page[-1]["Message"]["message_id"]
        if len(page) < page_size:
            break
    return pages


@dataclass
class _PendingAsk:
    """State for one in-flight `SmacApp.ask()` call."""

    prompt: str
    password: bool
    default: str | None
    event: threading.Event = field(default_factory=threading.Event)
    result: str = ""
    cancelled: bool = False


@dataclass
class _PendingPicker:
    """State for one in-flight `SmacApp.choose()` call."""

    items: list[tuple[str, str]]
    filterable: bool
    on_filter: Callable[[str], list[tuple[str, str]]] | None
    current_items: list[tuple[str, str]] = field(default_factory=list)
    event: threading.Event = field(default_factory=threading.Event)
    result: tuple[str, str] | None = None
    cancelled: bool = False


class FooterInput(Input):
    """The footer's ONE input bar.

    `Input` has no default bindings for Up/Down/Tab (confirmed against
    the installed `textual` package) and Escape is unclaimed too, so
    adding them here -- ahead of `Screen`'s own default `tab` ->
    `focus_next` binding in the focus-to-root binding chain Textual walks
    for every keypress -- is enough to make them ours without fighting
    the framework. `Enter` already reaches `SmacApp.on_input_submitted`
    via `Input`'s built-in `submit` binding.
    """

    BINDINGS = [
        Binding("up", "pullup_prev", "previous", show=False),
        Binding("down", "pullup_next", "next", show=False),
        Binding("tab", "pullup_complete", "complete", show=False),
        Binding("escape", "dismiss_or_cancel", "dismiss/cancel", show=False),
        # Overrides `Input`'s own `end`->`action_end` (cursor-to-end-of-line)
        # binding: the spec assigns `End` to "jump back to live and resume
        # auto-follow" unconditionally (§2 Body bullet), not text editing.
        Binding("end", "jump_to_live", "jump to live", show=False),
    ]

    def action_pullup_prev(self) -> None:
        self.app.pullup_move(-1)  # type: ignore[attr-defined]

    def action_pullup_next(self) -> None:
        self.app.pullup_move(1)  # type: ignore[attr-defined]

    def action_pullup_complete(self) -> None:
        self.app.pullup_complete()  # type: ignore[attr-defined]

    def action_dismiss_or_cancel(self) -> None:
        self.app.dismiss_or_cancel()  # type: ignore[attr-defined]

    def action_jump_to_live(self) -> None:
        self.app.jump_to_live()  # type: ignore[attr-defined]


class FeedLog(RichLog):
    """The body's `RichLog`, reporting every scroll-position change.

    `watch_scroll_y` fires on ANY change to the vertical scroll offset --
    mouse wheel, `PageUp`/`PageDown`/`End` (see `SmacApp`'s bindings and
    `FooterInput.action_jump_to_live`), or a programmatic `scroll_to`/
    `scroll_end` this module makes itself -- so it's the one hook
    `SmacApp` needs to implement the spec's auto-follow-pause +
    top-of-feed-loads-older behavior, regardless of what triggered the
    scroll. `getattr` (rather than an `isinstance` check against
    `SmacApp`, which is defined *after* this class) keeps this widget
    trivially reusable/testable outside a full `SmacApp` too.
    """

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        on_scroll = getattr(self.app, "_on_feed_scroll_changed", None)
        if callable(on_scroll):
            on_scroll()


class SmacApp(App[None]):
    """The `smac` TUI: header + scrollable body + one footer input."""

    BINDINGS = [
        Binding("pageup", "scroll_body_up", show=False),
        Binding("pagedown", "scroll_body_down", show=False),
        # Overrides Textual's own default Ctrl+C binding (`App.action_
        # help_quit`, just a "press ctrl+q instead" notice) -- the spec
        # says "Ctrl+C = same [as /quit]" (§0.2), an actual clean exit.
        # `priority=True` matches how `App` itself marks its own `ctrl+q`
        # binding, so this wins the same way over anything else bound to
        # the key (confirmed against the installed `textual` package).
        Binding("ctrl+c", "clean_quit", show=False, priority=True),
    ]

    CSS = """
    #header {
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
    }
    #body {
        height: 1fr;
    }
    #history-indicator {
        height: 1;
        background: $boost;
        color: $text-muted;
        padding: 0 1;
        display: none;
    }
    #pullup {
        height: auto;
        max-height: 10;
        border: round $accent;
        display: none;
    }
    #footer-input {
        height: 3;
    }
    """

    def __init__(self, api: SmacApi) -> None:
        super().__init__()
        self.api = api
        self.workspace_name: str | None = None
        self.current_channel_name: str | None = None
        self.current_channel_id: str | None = None
        self._pending_ask: _PendingAsk | None = None
        self._pending_picker: _PendingPicker | None = None
        self._filter_seq = 0
        self._log_lines: list[str] = []
        self._server_status: str | None = None
        self.header_text = "SMAC — not logged in"
        # -- live room state (SMAC-72 task 5) -------------------------------
        self._member_handles: dict[str, str] = {}
        self._channel_feed: ChannelFeed | None = None
        self._event_bell: EventBell | None = None
        self._following = True
        self._new_since_pause = 0
        self._oldest_loaded_message_id: str | None = None
        self._newest_loaded_message_id: str | None = None
        self._history_exhausted = False
        self._loading_older = False

    # -- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("SMAC — not logged in", id="header")
        yield FeedLog(id="body", wrap=True, markup=True, auto_scroll=True)
        yield Static("", id="history-indicator")
        yield OptionList(id="pullup")
        yield FooterInput(placeholder=_IDLE_PLACEHOLDER, id="footer-input")

    def on_mount(self) -> None:
        self.header = self.query_one("#header", Static)
        self.body = self.query_one("#body", FeedLog)
        self.history_indicator = self.query_one("#history-indicator", Static)
        self.pullup = self.query_one("#pullup", OptionList)
        self.footer_input = self.query_one("#footer-input", FooterInput)
        self.pullup.display = False
        self.footer_input.focus()
        self.run_worker(self._startup, thread=True)

    def on_unmount(self) -> None:
        """Clean shutdown of both background feeds (spec: sockets closed on
        `/quit`/exit) -- reached via Textual's own unmount pass, so this
        fires whether the app exits via `/quit`, Ctrl+C, or a test's
        `run_test()` context manager tearing down."""
        self._stop_channel_feed()
        if self._event_bell is not None:
            self._event_bell.stop()
            self._event_bell = None

    def action_scroll_body_up(self) -> None:
        self.body.scroll_page_up()

    def action_scroll_body_down(self) -> None:
        self.body.scroll_page_down()

    def action_clean_quit(self) -> None:
        """Ctrl+C: identical to `/quit` (spec §0.2: "Ctrl+C = same").

        Unlike `smac_cli.commands.cmd_quit` (built to run on a command
        worker thread, hence its own `call_from_thread`), a key binding's
        action runs on the event loop itself -- `system_line`'s `_call_ui`
        already tolerates being called from either (see its docstring),
        and `exit()` needs no thread-hop when we're already on that
        thread.
        """
        self.system_line("goodbye — session saved, see you next time")
        self.exit()

    # -- thread-safety helper ----------------------------------------------

    def _call_ui(self, fn: Callable[[], None]) -> None:
        """Run `fn` on the event loop thread, from wherever we're called.

        Command handlers (and the things they call: `system_line`,
        `set_header`, ...) run on a worker thread; the handful of call
        sites that are already on the event loop (message handlers) just
        run `fn` directly -- `call_from_thread` raises `RuntimeError` when
        called from the app's own thread, which is exactly the signal
        this needs to fall back on.
        """
        try:
            self.call_from_thread(fn)
        except RuntimeError:
            fn()

    # -- public helpers for smac_cli.commands ------------------------------

    def system_line(self, text: str) -> None:
        """Render `text` as a dim, `── ── `-wrapped system line in the body."""

        def _do() -> None:
            self._log_lines.append(f"── {text} ──")
            self.body.write(f"[dim]── {escape(text)} ──[/dim]")

        self._call_ui(_do)

    def set_header(self, text: str) -> None:
        """Set the header bar to `text` verbatim (no workspace/channel state)."""

        def _do() -> None:
            self.header_text = text
            self.header.update(text)

        self._call_ui(_do)

    def enter_workspace(self, workspace_name: str, channel_name: str) -> None:
        """Land in `workspace_name`'s `channel_name`: sets state + header."""
        self.workspace_name = workspace_name
        self.current_channel_name = channel_name
        self.current_channel_id = None
        self.set_header(f"{workspace_name} — #{channel_name}")

    def reset_to_logged_out(self, message: str) -> None:
        """`/workspace delete`'s success path (SMAC-72 task 6): the mirror
        image of `enter_workspace`/`enter_general` -- tear the session
        down completely and land back on the Frame-1 welcome screen with
        `message` as the trailing system line.

        Stops both live-room background threads, drops the in-memory and
        on-disk session, clears every piece of per-workspace state this
        app instance was holding, and re-renders the welcome screen from
        a blank body (nothing from the deleted workspace's feed should
        linger once it's gone). Must be called from a worker thread, same
        as every other command handler -- `_call_ui`/`system_line`/
        `set_header` (used by `_show_welcome_screen`) all handle that.
        """
        self._stop_channel_feed()
        if self._event_bell is not None:
            self._event_bell.stop()
            self._event_bell = None
        self.api.session = None
        session_path().unlink(missing_ok=True)
        self.workspace_name = None
        self.current_channel_name = None
        self.current_channel_id = None
        self._member_handles = {}

        def _clear_body() -> None:
            self._log_lines.clear()
            self.body.clear()

        self._call_ui(_clear_body)
        self._show_welcome_screen()
        self.system_line(message)

    # -- the live room (SMAC-72 task 5) ------------------------------------
    #
    # `enter_general`/`enter_channel` must be called from a worker thread --
    # every step is blocking (HTTP history/mark-read calls, a WebSocket
    # connect). `enter_workspace` above only ever sets the header + names;
    # the channel content itself (history, the live feed, mark-read, the
    # member directory) is this section's job, called right after
    # `enter_workspace` by every login/register/restore path (`smac_cli.
    # commands.cmd_register`/`cmd_login`, `SmacApp._restore_session`) and by
    # `/channel <name>` (`smac_cli.commands.cmd_channel`).

    def enter_general(self) -> None:
        """Resolve the workspace's `general` channel and enter it.

        The landing spot after every register/login/session-restore
        (spec Frame 4/7/8). A channel-lookup hiccup here shouldn't strand
        the caller mid-login -- it's reported as a system line rather than
        raised, leaving the header `enter_workspace` already set in place.
        """
        try:
            channels = self.api.channels()
        except SmacError as exc:
            self.system_line(exc.message)
            return
        match = next(
            (c for c in channels if c["channel_name"].lower() == "general"), None
        )
        if match is None:
            self.system_line("no #general channel found")
            return
        self.enter_channel(match["channel_id"], match["channel_name"])

    def enter_channel(self, channel_id: str, channel_name: str) -> None:
        """Switch the live room to `channel_id`: history, feed, mark-read.

        Stops any previous channel's feed, loads the channel's recent
        history (oldest-first; see `_walk_message_pages` for why "recent"
        requires walking forward from the beginning), marks the channel
        read, then attaches the live feed and (the first time only) the
        mention bell.

        The body is only *cleared* when this is an actual channel switch
        (`current_channel_id` was already set to something) -- the very
        first entry of a session, right after register/login/restore, is
        called with the body already showing that flow's own banners
        (spec Frame 4: "account created" / "workspace founded" stay on
        screen once landed in #general), and those aren't this channel's
        content to wipe.
        """
        is_switch = self.current_channel_id is not None
        self._stop_channel_feed()
        self.current_channel_id = channel_id
        self.current_channel_name = channel_name
        self._following = True
        self._new_since_pause = 0
        self._history_exhausted = False
        self._loading_older = False
        self._oldest_loaded_message_id = None
        self._newest_loaded_message_id = None
        self.set_header(f"{self.workspace_name} — #{channel_name}")

        def _prepare_body() -> None:
            if is_switch:
                self._log_lines.clear()
                self.body.clear()
            self.body.auto_scroll = True
            self._hide_history_indicator()

        self._call_ui(_prepare_body)

        self._refresh_member_handles()

        try:
            pages = _walk_message_pages(self.api, channel_id)
        except SmacError as exc:
            self.system_line(exc.message)
            pages = []
        recent = pages[-1] if pages else []
        for payload in recent:
            self._enrich_sender_handle(payload)
        lines = [message_line(payload) for payload in recent]

        def _populate() -> None:
            self._log_lines.extend(lines)
            for line in lines:
                self.body.write(escape(line))

        self._call_ui(_populate)
        if recent:
            self._oldest_loaded_message_id = recent[0]["Message"]["message_id"]
            self._newest_loaded_message_id = recent[-1]["Message"]["message_id"]

        try:
            self.api.mark_read(channel_id)
        except SmacError:
            pass

        self._start_channel_feed(channel_id)
        self._ensure_event_bell()

    def _refresh_member_handles(self) -> None:
        """Rebuild the `member_id -> handle` directory (blocking; worker-
        thread only) used to enrich a message payload's `Sender` before
        rendering -- see `smac_cli.render`'s module docstring for why the
        payload alone doesn't carry the handle. A failure here just means
        rendering falls back to `member_name` for now; it never blocks
        entering the channel."""
        try:
            members = self.api.members()
        except SmacError:
            return
        self._member_handles = {m["member_id"]: m["handle"] for m in members}

    def _enrich_sender_handle(self, payload: dict[str, Any]) -> None:
        """Inject `payload["Sender"]["handle"]` from `_member_handles`, if
        known and not already present. Safe to call on any payload shape;
        a payload without a `Sender` dict (shouldn't happen, but this is
        defensive) is left untouched."""
        sender = payload.get("Sender")
        if not isinstance(sender, dict) or sender.get("handle"):
            return
        handle = self._member_handles.get(sender.get("member_id", ""))
        if handle:
            sender["handle"] = handle

    # -- channel feed: attach/detach, message delivery, reconnect ----------

    def _stop_channel_feed(self) -> None:
        if self._channel_feed is not None:
            self._channel_feed.stop()
            self._channel_feed = None

    def _start_channel_feed(self, channel_id: str) -> None:
        def provider() -> str:
            return self.api.ws_channel_url(channel_id)

        def deliver(payload: dict[str, Any]) -> None:
            self._deliver_from_feed_thread(
                self._handle_channel_payload, channel_id, payload
            )

        feed = ChannelFeed(provider, deliver)
        self._channel_feed = feed
        feed.start()

    def _deliver_from_feed_thread(self, fn: Callable[..., None], *args: Any) -> None:
        """`call_from_thread(fn, *args)`, tolerating an already-closed loop.

        `ChannelFeed`/`EventBell`'s own `stop()` (`smac_cli/live.py`)
        closes the live socket to interrupt an in-flight `recv()`
        promptly -- which, on a background thread, raises locally and
        calls this feed's `_on_disconnected` hook (a synthetic
        `{"event": "disconnected"}` delivery) essentially immediately.
        `on_unmount` calls `_stop_channel_feed()`/`_event_bell.stop()` as
        part of Textual's OWN shutdown sequence, so there's a real window
        where that synthetic delivery lands on this thread just as (or
        just after) the app's event loop has already closed -- observed
        in practice via `RuntimeError: Event loop is closed` bubbling up
        as an unhandled exception on a daemon thread when a live channel
        feed is torn down (`/quit` or a test's `run_test()` exiting) at
        the same moment the underlying server connection also drops.
        There is nothing useful to do with a payload once the app itself
        is gone, so this is a deliberate, narrow swallow -- not a
        `SmacError`, not a `SmacApi`/network failure, just "too late."
        """
        try:
            self.call_from_thread(fn, *args)
        except RuntimeError:
            pass

    def _handle_channel_payload(self, channel_id: str, payload: dict[str, Any]) -> None:
        """Runs on the event loop thread (`call_from_thread`'d by `_start_
        channel_feed`'s `deliver`). Drops anything from a feed the app has
        already switched away from (`channel_id` no longer matches
        `current_channel_id`) -- that feed's `stop()` was already
        requested, but a message already in flight when the switch
        happened can still arrive after."""
        if self.current_channel_id != channel_id:
            return

        event = payload.get("event")
        if event == "disconnected":
            self.system_line("channel disconnected — reconnecting")
            return
        if event == "reconnected":
            self.system_line("channel reconnected")
            self.run_worker(
                lambda: self._refresh_after_reconnect(channel_id),
                thread=True,
                group="reconnect-refresh",
            )
            return

        self._enrich_sender_handle(payload)
        line = message_line(payload)
        self._log_lines.append(line)
        self.body.write(escape(line))
        self._newest_loaded_message_id = payload["Message"]["message_id"]
        if self._oldest_loaded_message_id is None:
            self._oldest_loaded_message_id = payload["Message"]["message_id"]
        if self._following:
            self._new_since_pause = 0
            self.run_worker(
                lambda: self._safe_mark_read(channel_id), thread=True, group="mark-read"
            )
        else:
            self._new_since_pause += 1
            self._update_history_indicator()

    def _refresh_after_reconnect(self, channel_id: str) -> None:
        """Catch up on whatever was missed while `channel_id`'s feed was
        down (spec: "history-refresh on reattach"). Fetches only the gap
        -- everything after the newest message already shown -- and
        APPENDS it, exactly like a normal live arrival; the mockup (spec
        §0, top-of-file) shows "── channel reconnected ──" landing among
        existing history, not replacing it. Runs on a worker thread;
        applies the result on the UI thread only if the app hasn't since
        switched to a different channel.
        """
        anchor = self._newest_loaded_message_id
        try:
            if anchor is not None:
                missed = self._walk_forward_from(channel_id, anchor)
            else:
                pages = _walk_message_pages(self.api, channel_id)
                missed = pages[-1] if pages else []
        except SmacError as exc:
            self._call_ui(lambda: self.system_line(exc.message))
            return
        for payload in missed:
            self._enrich_sender_handle(payload)
        lines = [message_line(payload) for payload in missed]

        def apply() -> None:
            if self.current_channel_id != channel_id:
                return
            for payload, line in zip(missed, lines):
                self._log_lines.append(line)
                self.body.write(escape(line))
                if self._oldest_loaded_message_id is None:
                    self._oldest_loaded_message_id = payload["Message"]["message_id"]
                self._newest_loaded_message_id = payload["Message"]["message_id"]
            if self._following:
                self._new_since_pause = 0
            elif missed:
                self._new_since_pause += len(missed)
                self._update_history_indicator()

        self._call_ui(apply)

    def _walk_forward_from(
        self, channel_id: str, after_message_id: str
    ) -> list[dict[str, Any]]:
        """Every message after `after_message_id`, chaining forward pages
        (each capped at `DEFAULT_MESSAGE_LIMIT` server-side) until caught
        up. Used for the reconnect gap-fill, where the anchor is already
        known -- unlike `_walk_message_pages`, this always walks from
        `after_message_id` (never from the beginning) and keeps every
        page, since the whole gap (not just the last page of it) needs
        to be appended."""
        missed: list[dict[str, Any]] = []
        after: str | None = after_message_id
        while True:
            page = self.api.messages(
                channel_id, after=after, limit=DEFAULT_MESSAGE_LIMIT
            )
            if not page:
                break
            missed.extend(page)
            after = page[-1]["Message"]["message_id"]
            if len(page) < DEFAULT_MESSAGE_LIMIT:
                break
        return missed

    def _safe_mark_read(self, channel_id: str) -> None:
        try:
            self.api.mark_read(channel_id)
        except SmacError:
            pass

    # -- mention bell -------------------------------------------------------

    def _ensure_event_bell(self) -> None:
        """Attach the member's private mention-events feed, once per
        session (idempotent -- called from every `enter_channel`, but a
        session only ever needs one)."""
        if self._event_bell is not None:
            return

        def provider() -> str:
            return self.api.ws_events_url()

        bell = EventBell(provider, self._deliver_mention_event)
        self._event_bell = bell
        bell.start()

    def _deliver_mention_event(self, event: dict[str, Any]) -> None:
        self._deliver_from_feed_thread(self._handle_mention_event, event)

    def _handle_mention_event(self, event: dict[str, Any]) -> None:
        """A mention in ANY channel rings the bell -- EXCEPT the currently
        open one, where the mention is already visible as an ordinary
        message line (its `<@id>` token already renders as `@handle` via
        `message_line`); ringing the bell for it too would just be a
        duplicate of what's already on screen."""
        message = event.get("message", {})
        channel = message.get("Channel", {})
        if channel.get("channel_id") == self.current_channel_id:
            return
        self._enrich_sender_handle(message)
        line = bell_line(event)
        self._log_lines.append(line)
        self.body.write(escape(line))

    # -- scrolling: auto-follow pause/resume + top-of-feed load-older ------

    def _on_feed_scroll_changed(self) -> None:
        """`FeedLog.watch_scroll_y`'s hook -- fires on every scroll change
        regardless of cause (wheel, PageUp/PageDown/End, or a programmatic
        scroll this module made itself)."""
        if self.current_channel_id is None:
            return
        at_bottom = self.body.is_vertical_scroll_end
        if at_bottom and not self._following:
            self._resume_following()
        elif not at_bottom and self._following:
            self._pause_following()
        # Only treat "scroll_y is 0" as a deliberate top-of-feed gesture
        # when there's actually more content than fits on screen -- for a
        # short history (the common case in tests), 0 is simultaneously
        # the top AND the bottom, and that's never a "load older" signal.
        if self.body.max_scroll_y > 0 and self.body.scroll_y <= 0:
            self._load_older_history()

    def _pause_following(self) -> None:
        self._following = False
        self._new_since_pause = 0
        self.body.auto_scroll = False
        self._update_history_indicator()

    def _resume_following(self) -> None:
        self._following = True
        self._new_since_pause = 0
        self.body.auto_scroll = True
        self._hide_history_indicator()
        channel_id = self.current_channel_id
        if channel_id is not None:
            self.run_worker(
                lambda: self._safe_mark_read(channel_id), thread=True, group="mark-read"
            )

    def _update_history_indicator(self) -> None:
        text = (
            f"── viewing history — {self._new_since_pause} new below "
            "(End to jump) ──"
        )

        def _do() -> None:
            self.history_indicator.update(text)
            self.history_indicator.display = True

        self._call_ui(_do)

    def _hide_history_indicator(self) -> None:
        def _do() -> None:
            self.history_indicator.display = False

        self._call_ui(_do)

    def jump_to_live(self) -> None:
        """`End` (or sending a message): jump to the bottom and resume
        auto-follow, per the spec's Body bullet."""
        self.body.auto_scroll = True
        self.body.scroll_end(animate=False)
        self._resume_following()

    def _load_older_history(self) -> None:
        """Top-of-feed reached: fetch and prepend the page immediately
        preceding what's currently shown (see `_walk_message_pages`).
        Guarded against re-entry (`_loading_older`) and against repeating
        a walk once the true beginning of the channel has been reached
        (`_history_exhausted`)."""
        if self._loading_older or self._history_exhausted:
            return
        anchor = self._oldest_loaded_message_id
        channel_id = self.current_channel_id
        if anchor is None or channel_id is None:
            return
        self._loading_older = True

        def work() -> None:
            try:
                pages = _walk_message_pages(self.api, channel_id, stop_before_id=anchor)
            except SmacError:
                pages = []
            older = pages[-1] if pages else []

            def apply() -> None:
                self._loading_older = False
                if self.current_channel_id != channel_id:
                    return
                if not older:
                    self._history_exhausted = True
                    return
                self._prepend_history(older)

            self._call_ui(apply)

        self.run_worker(work, thread=True, group="load-older")

    def _prepend_history(self, older: list[dict[str, Any]]) -> None:
        for payload in older:
            self._enrich_sender_handle(payload)
        new_lines = [message_line(payload) for payload in older]
        self._log_lines = new_lines + self._log_lines
        self.body.clear()
        for line in self._log_lines:
            self.body.write(escape(line))
        self._oldest_loaded_message_id = older[0]["Message"]["message_id"]
        # Keep the view where the user was, roughly: the content just grew
        # by `len(new_lines)` lines above the previously-topmost line, so
        # scroll down by the same amount to keep that line in the same
        # screen position rather than yanking the view to a new spot.
        self.body.scroll_to(y=self.body.scroll_y + len(new_lines), animate=False)

    def ask(
        self, prompt: str, *, password: bool = False, default: str | None = None
    ) -> str:
        """Blocking inline Q&A: must be called from a worker thread.

        Shows `prompt` as the footer's placeholder (masking input if
        `password`); blocks the CALLING thread (not the event loop) until
        Enter or Escape is pressed. An empty Enter returns `default` (or
        `""` if no default was given). Escape raises `FormCancelled`.
        """
        pending = _PendingAsk(prompt=prompt, password=password, default=default)
        self.call_from_thread(self._begin_ask, pending)
        pending.event.wait()
        if pending.cancelled:
            raise FormCancelled()
        return pending.result

    def choose(
        self,
        items: list[tuple[str, str]],
        *,
        filterable: bool = False,
        on_filter: Callable[[str], list[tuple[str, str]]] | None = None,
    ) -> tuple[str, str]:
        """Blocking inline pick-one-of-many (the workspace picker + join frame).

        `items` are `(id, label)` pairs shown in the pull-up. If
        `filterable`, typed text re-queries via `on_filter(text)` (run on
        its own worker thread so a live network search never blocks the
        event loop) and repopulates the list. Returns the chosen `(id,
        label)`. Escape raises `FormCancelled` -- same contract as `ask()`,
        so a single `try`/`except FormCancelled` in `SmacApp._run_command`
        covers every cancellable step of every auth flow uniformly; a
        handler that caught `None` here itself (as an earlier version of
        this method returned) could forget to reset the header on cancel,
        which is exactly the bug this raise-instead-of-None design avoids.
        Must be called from a worker thread, same as `ask()`.
        """
        picker = _PendingPicker(items=items, filterable=filterable, on_filter=on_filter)
        self.call_from_thread(self._begin_picker, picker)
        picker.event.wait()
        if picker.cancelled:
            raise FormCancelled()
        assert picker.result is not None
        return picker.result

    def post_current(self, text: str) -> None:
        """The footer contract's hook for a non-'/' send while logged in.

        Sending always jumps back to live and resumes auto-follow first
        (spec's Body bullet), then posts on a worker thread -- the
        message itself arrives back through the channel feed's own
        self-echo, never appended directly here. A 429
        (`RateLimitedError`) shows the server's message AND restores the
        typed text into the input so it's never silently lost; any other
        `SmacError` just shows the server's message.
        """
        if self.current_channel_id is None:
            self.system_line("not in a channel yet — /channel <name>")
            return

        channel_id = self.current_channel_id
        self._call_ui(self.jump_to_live)

        def work() -> None:
            try:
                self.api.post(channel_id, text)
            except RateLimitedError as exc:

                def _restore() -> None:
                    self.system_line(exc.message)
                    self.footer_input.value = text
                    self.footer_input.cursor_position = len(text)

                self._call_ui(_restore)
            except SmacError as exc:
                self.system_line(exc.message)

        self.run_worker(work, thread=True)

    # -- ask()/choose() plumbing (main-thread side) ------------------------

    def _idle_placeholder(self) -> str:
        if self.api.session is None:
            return _IDLE_PLACEHOLDER
        return "type a message, or / for commands"

    def _begin_ask(self, pending: _PendingAsk) -> None:
        self._pending_ask = pending
        self._hide_pullup()
        self.footer_input.password = pending.password
        self.footer_input.value = ""
        self.footer_input.placeholder = pending.prompt

    def _begin_picker(self, picker: _PendingPicker) -> None:
        self._pending_picker = picker
        self.footer_input.password = False
        self.footer_input.value = ""
        self.footer_input.placeholder = (
            "type to search" if picker.filterable else "↑/↓ select, Enter to open"
        )
        picker.current_items = picker.items
        self._show_pullup(picker.items)

    def _on_picker_filter_changed(self, value: str) -> None:
        picker = self._pending_picker
        if picker is None or not picker.filterable or picker.on_filter is None:
            return
        on_filter = picker.on_filter
        self._filter_seq += 1
        seq = self._filter_seq

        def work() -> None:
            try:
                items = on_filter(value)
            except SmacError:
                items = []

            def apply() -> None:
                if self._pending_picker is not picker or seq != self._filter_seq:
                    return
                picker.current_items = items
                self._show_pullup(items)

            self._call_ui(apply)

        self.run_worker(work, thread=True, group="filter")

    # -- pull-up (command suggestions + picker items share one widget) ----

    def _show_pullup(self, items: list[tuple[str, str]]) -> None:
        self.pullup.clear_options()
        for identifier, label in items:
            self.pullup.add_option(Option(label, id=identifier))
        if items:
            self.pullup.highlighted = 0
        self.pullup.display = bool(items)

    def _hide_pullup(self) -> None:
        self.pullup.display = False

    def _update_command_pullup(self, typed: str) -> None:
        typed_lower = typed.lower()
        matches = [
            (name, f"/{name}   {help_text}")
            for name, (_, help_text) in COMMANDS.items()
            if name.startswith(typed_lower)
        ]
        self._show_pullup(matches)

    def pullup_move(self, delta: int) -> None:
        """Move the pull-up's highlight -- called by `FooterInput`'s Up/Down."""
        if not self.pullup.display:
            return
        if delta < 0:
            self.pullup.action_cursor_up()
        else:
            self.pullup.action_cursor_down()

    def pullup_complete(self) -> None:
        """Tab: complete the highlighted suggestion into the input (command mode only)."""
        if not self.pullup.display or self._pending_picker is not None:
            return
        idx = self.pullup.highlighted
        if idx is None:
            return
        option = self.pullup.get_option_at_index(idx)
        self.footer_input.value = f"/{option.id} "
        self.footer_input.cursor_position = len(self.footer_input.value)

    def dismiss_or_cancel(self) -> None:
        """Escape: cancel a pending ask/picker, else just dismiss the pull-up."""
        if self._pending_ask is not None:
            pending = self._pending_ask
            self._pending_ask = None
            pending.cancelled = True
            self.footer_input.password = False
            self.footer_input.value = ""
            self.footer_input.placeholder = self._idle_placeholder()
            pending.event.set()
            return
        if self._pending_picker is not None:
            picker = self._pending_picker
            self._pending_picker = None
            picker.cancelled = True
            self._hide_pullup()
            self.footer_input.value = ""
            self.footer_input.placeholder = self._idle_placeholder()
            picker.event.set()
            return
        if self.pullup.display:
            self._hide_pullup()

    # -- footer input contract ---------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._pending_ask is not None:
            return
        if self._pending_picker is not None:
            self._on_picker_filter_changed(event.value)
            return
        if event.value.startswith("/"):
            self._update_command_pullup(event.value[1:])
        else:
            self._hide_pullup()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value
        footer = event.input

        if self._pending_ask is not None:
            pending = self._pending_ask
            self._pending_ask = None
            pending.result = value if value != "" else (pending.default or "")
            footer.password = False
            footer.value = ""
            footer.placeholder = self._idle_placeholder()
            pending.event.set()
            return

        if self._pending_picker is not None:
            picker = self._pending_picker
            idx = self.pullup.highlighted
            if idx is None or not picker.current_items:
                return
            self._pending_picker = None
            self._hide_pullup()
            footer.value = ""
            footer.placeholder = self._idle_placeholder()
            picker.result = picker.current_items[idx]
            picker.event.set()
            return

        if value == "":
            return

        if value.startswith("/"):
            self._dispatch_command(value[1:])
            footer.value = ""
            return

        self._hide_pullup()
        footer.value = ""
        if self.api.session is None:
            self.system_line("not logged in — /register or /login")
        else:
            self.post_current(value)

    def _dispatch_command(self, text: str) -> None:
        parts = text.split(" ", 1)
        typed_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        name = typed_name
        if self.pullup.display and self.pullup.option_count:
            idx = self.pullup.highlighted
            if idx is not None:
                candidate = str(self.pullup.get_option_at_index(idx).id)
                if candidate in COMMANDS:
                    name = candidate

        self._hide_pullup()
        entry = COMMANDS.get(name)
        if entry is None:
            self.system_line(f"unknown command: /{typed_name}")
            return
        handler, _ = entry
        self._run_command(handler, args)

    def _run_command(
        self, handler: Callable[["SmacApp", str], None], args: str
    ) -> None:
        def work() -> None:
            try:
                handler(self, args)
            except FormCancelled:
                if self.api.session is None:
                    self.set_header("SMAC — not logged in")
            except SmacError as exc:
                self.system_line(exc.message)

        self.run_worker(work, thread=True, exclusive=True, group="command")

    # -- startup: welcome screen / session restore / version handshake ----

    def _show_welcome_screen(self) -> None:
        """Render the logged-out welcome screen (spec Frame 1): header +
        banner + the two entry commands + the server status line."""
        self.set_header("SMAC — not logged in")
        self.write_line("")
        self.write_line("Welcome to SMAC — a place for your agents to meet.")
        self.write_line("")
        self.write_line("/register   create your account + workspace")
        self.write_line("/login      log in (email + password)")
        self.write_line("")
        self.write_line(self._server_status or f"server: {self.api.url}")

    def write_line(self, text: str) -> None:
        def _do() -> None:
            self._log_lines.append(text)
            self.body.write(escape(text) if text else "")

        self._call_ui(_do)

    def _startup(self) -> None:
        """Runs on a worker thread from `on_mount`: version handshake, then
        either straight into the restored session or the welcome screen.
        """
        mismatch = self._check_version()
        if self.api.session is not None:
            self._restore_session()
        else:
            self._show_welcome_screen()
        if mismatch:
            self.system_line(mismatch)

    def _check_version(self) -> str | None:
        """Fetch `/meta`, set `self._server_status`, and return the
        version-mismatch system-line text (spec Decision 6), or `None` if
        the versions match (or the server couldn't be reached at all)."""
        try:
            meta = self.api.meta()
        except Unreachable:
            self._server_status = (
                f"server: {self.api.url} — not reachable — run: smac-server --start"
            )
            return None
        except SmacError as exc:
            self._server_status = exc.message
            return None
        server_version = str(meta.get("server_version", "?"))
        self._server_status = f"server: {self.api.url} ✓ running (v{server_version})"
        if server_version != CLIENT_VERSION:
            return (
                f"server {server_version}, client {CLIENT_VERSION} — "
                "update: git pull && pip install -e ."
            )
        return None

    def _restore_session(self) -> None:
        session = self.api.session
        assert session is not None
        try:
            self.api.whoami()
        except SessionExpired:
            self._show_welcome_screen()
            self.system_line("session expired — /login")
            return
        except SmacError as exc:
            self._show_welcome_screen()
            self.system_line(exc.message)
            return
        name = cached_workspace_name(session.workspace_id) or session.workspace_id
        self.enter_workspace(name, "general")
        self.enter_general()


def main() -> None:
    """Entry point for the `smac` console script."""
    session = Session.load(session_path())
    url = session.url if session is not None else DEFAULT_URL
    api = SmacApi(url, session=session)
    app = SmacApp(api)
    app.run()
