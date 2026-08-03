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
from typing import Callable

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from smac_cli import CLIENT_VERSION
from smac_cli.api import Session, SmacApi
from smac_cli.commands import COMMANDS
from smac_cli.errors import SessionExpired, SmacError, Unreachable
from smac_cli.paths import config_dir, session_path

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
    ]

    def action_pullup_prev(self) -> None:
        self.app.pullup_move(-1)  # type: ignore[attr-defined]

    def action_pullup_next(self) -> None:
        self.app.pullup_move(1)  # type: ignore[attr-defined]

    def action_pullup_complete(self) -> None:
        self.app.pullup_complete()  # type: ignore[attr-defined]

    def action_dismiss_or_cancel(self) -> None:
        self.app.dismiss_or_cancel()  # type: ignore[attr-defined]


class SmacApp(App[None]):
    """The `smac` TUI: header + scrollable body + one footer input."""

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

    # -- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("SMAC — not logged in", id="header")
        yield RichLog(id="body", wrap=True, markup=True, auto_scroll=True)
        yield OptionList(id="pullup")
        yield FooterInput(placeholder=_IDLE_PLACEHOLDER, id="footer-input")

    def on_mount(self) -> None:
        self.header = self.query_one("#header", Static)
        self.body = self.query_one("#body", RichLog)
        self.pullup = self.query_one("#pullup", OptionList)
        self.footer_input = self.query_one("#footer-input", FooterInput)
        self.pullup.display = False
        self.footer_input.focus()
        self.run_worker(self._startup, thread=True)

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

        Channel resolution and the real `POST` are Task 5's job; this
        task has no way to learn a channel_id yet (no `/channel` command,
        no `channels()` call on login), so `current_channel_id` is always
        `None` here and this simply says so.
        """
        if self.current_channel_id is None:
            self.system_line(
                "not in a channel yet — channel support lands in a later task"
            )
            return

        channel_id = self.current_channel_id

        def work() -> None:
            try:
                self.api.post(channel_id, text)
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


def main() -> None:
    """Entry point for the `smac` console script."""
    session = Session.load(session_path())
    url = session.url if session is not None else DEFAULT_URL
    api = SmacApi(url, session=session)
    app = SmacApp(api)
    app.run()
