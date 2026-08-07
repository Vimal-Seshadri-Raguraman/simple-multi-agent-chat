"""`AgentApp`: the two-pane Textual TUI -- the default (no-flags) experience
(design doc §3). Left pane (`#inner`) is a timestamped trace of every bus
event -- the "inner view" into what the agent's brain is doing, for both
the SMAC mention loop and direct chat, since both funnel through the same
`brain.think()` (design doc: "One brain, two conversations, one inner view
over both."). Right pane (`#chat`) is the direct-chat transcript. A footer
`Input` sends typed text to `agent.chat()` -- NEVER to SMAC; `Agent.chat`
itself already guarantees that (see `agent.py`'s docstring), this module
just never gives the input box any other place to go.

Two asyncio tasks run alongside the Textual event loop, started in
`on_mount` and cancelled-and-awaited in `on_unmount` (Textual dispatches
`Unmount` to the App itself during `_shutdown()`, so this is the correct
symmetric hook -- see the module docstring of `main.py`'s `_run_headless`
for the same cancel-then-await idiom this mirrors):

- `_follow_bus` -- lives for the app's whole life, rendering every live
  bus event as it arrives.
- `self.agent.run()` -- the real mention loop. Nothing else starts it on
  this path (`--headless`/`--chat-only`/`--once` each manage their own
  loop in `main.py`), so the TUI is responsible for it here.

Both are cancelled on quit so the app never leaves a dangling task behind
(`Bus.subscribe()`'s own docstring: cancelling the task holding it
unsubscribes cleanly via its `finally`).

SECURITY (constitution §7.5, design doc §5): every string that
originates from SMAC (message text, sender names, the join response's
handle/workspace) is untrusted input, and it goes through TWO
independent defenses before it reaches a widget:

1. `Text.append()` never parses Rich/Textual MARKUP regardless of what
   the string contains, plus `markup=False` on both `RichLog`s as
   defense in depth -- a message containing `[bold red]PWNED[/]` must
   appear on screen as that literal text, not styled. This says nothing
   about raw terminal bytes, though: `rich.control.STRIP_CONTROL_CODES`
   only strips BEL/BS/VT/FF/CR (7, 8, 11, 12, 13) -- ESC (0x1b) is NOT
   one of them -- and Textual's `Strip.render_style()` embeds segment
   text raw into `f"\x1b[{ansi}m{text}\x1b[0m"`, which the compositor
   writes straight to the terminal fd. Left alone, a `member_name` or
   message containing `\x1b]0;spoofed-title\x1b\\` (title-bar spoof),
   `\x1b[2J\x1b[H` (clear/reposition), or an OSC52 payload (clipboard
   write -- Textual itself uses OSC52, so any target terminal that can
   run this TUI already supports it) reaches the operator's real
   terminal.
2. `sanitize()` below is the actual control-byte defense: it strips or
   visibly escapes C0/C1 control bytes (ESC and DEL included) and
   redacts obvious key-shaped tokens before a SMAC-sourced string is
   handed to `Text.append()` at all. It is applied at the two places
   SMAC-sourced strings become widget content: `_format_event` (every
   bus event field) and `_render_header` (handle/workspace, which come
   from the server's join response -- untrusted if `SMAC_URL` points
   somewhere hostile).

No credential (SMAC API key, Anthropic key) is ever deliberately placed
in a bus event's `fields` (see `bus.py`'s module docstring) -- the
header shows only the agent's public handle, workspace name, and
connection state -- but `sanitize()`'s key-shaped-token redaction is
defense in depth against a secret ending up in free-form text anyway
(e.g. an SDK exception message that happens to echo part of a key).

Colors come from `design/tokens.json` (the design-system constitution),
read once at runtime by `_dark_palette()` below -- never hardcoded hex --
so a change to the constitution is the only place a color ever needs to
change. Missing the file is a loud `FileNotFoundError` naming the path,
not a silent fallback to some other palette.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import re
from pathlib import Path
from typing import Any, Protocol

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Input, RichLog, Static

from analyst_agent.bus import Bus, Event

#: `examples/analyst_agent/tui.py` -> repo root is two `parent`s up
#: (`analyst_agent/`, `examples/`). Resolved fresh on every call (not
#: cached into a module-level constant) so a test can monkeypatch
#: `Path(__file__)`... in practice it never needs to: this only depends
#: on where this file lives on disk, which doesn't change at runtime.
_TOKENS_PATH = Path(__file__).resolve().parents[2] / "design" / "tokens.json"

#: How many past bus events to replay into the panes on mount, so a late
#: attach (or a TUI restarted mid-run) isn't blank. Matches the order of
#: magnitude of `bus.py`'s own backlog cap without assuming the two stay
#: numerically equal.
_HISTORY_SEED = 200

# -- sanitize(): the control-byte / secret-token choke point ---------------
#
# See the module docstring's SECURITY section for *why* this exists --
# `Text.append()` + `markup=False` neutralize Rich/Textual markup but do
# nothing about raw ESC bytes reaching the terminal. This is the actual
# fix for that.

#: Every C0 control byte except tab/newline/CR (those get the
#: whitespace-collapse policy below, not an escape), DEL, and every C1
#: control byte -- `\x00-\x08`, `\x0b-\x1f` (skips \t=09, \n=0a),
#: `\x7f-\x9f`. This deliberately includes ESC (0x1b), which is the
#: byte `rich.control.STRIP_CONTROL_CODES` (7, 8, 11, 12, 13) does NOT
#: cover -- see the module docstring.
_CONTROL_BYTE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

#: Obvious key-shaped tokens: known secret prefixes (Anthropic, generic
#: `sk-`, this project's own SMAC keys, and the handful of other vendor
#: prefixes worth catching for free) followed by 4+ token characters.
#: Not a substitute for "never put a secret in a bus event" (`bus.py`'s
#: own invariant) -- this is defense in depth for the case where one
#: leaks into free-form text anyway (e.g. an SDK exception message).
_SECRET_TOKEN = re.compile(
    r"\b(?:sk-ant-|sk-|smac-|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|"
    r"xox[baprs]-|AKIA|AIza)[A-Za-z0-9_-]{4,}\b"
)

_REDACTED = "[REDACTED]"


def sanitize(text: str) -> str:
    """Neutralize `text` before it reaches a widget. Every SMAC-sourced
    string (message bodies, sender/handle/workspace names -- anything
    that ultimately came from `SMAC_URL`, which is untrusted) MUST pass
    through this before `Text.append()`/`Static.update()` ever sees it.

    Policy, applied in order:

    1. Tab and newline (`\\t`, `\\n`, `\\r\\n`, `\\r`) collapse to a
       single space. Both panes are line-oriented -- one
       `RichLog.write()` per event -- so a raw newline in a SMAC
       message would otherwise split one event across multiple visual
       lines and desync the trace from what actually happened; a
       collapsed space keeps the line-per-event invariant intact
       without losing the text.
    2. Every other C0 control byte (0x00-0x08, 0x0b-0x1f), DEL (0x7f),
       and every C1 control byte (0x80-0x9f) -- ESC (0x1b) included --
       is replaced with its visible `\\xHH` escape, not silently
       dropped. Visible-but-inert beats invisible-but-gone: an
       attempted injection (a title-bar spoof, a screen clear, an
       OSC52 clipboard write -- see the module docstring) shows up on
       screen as harmless literal text instead of vanishing without a
       trace.
    3. An obvious key-shaped token (`sk-ant-...`, `sk-...`, `smac-...`,
       etc. -- see `_SECRET_TOKEN`) is replaced with `[REDACTED]`.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", " ").replace("\t", " ")
    text = _CONTROL_BYTE.sub(lambda m: f"\\x{ord(m.group()):02x}", text)
    return _SECRET_TOKEN.sub(_REDACTED, text)


def _field(fields: dict[str, Any], key: str, default: str = "?") -> str:
    """`sanitize(str(fields.get(key, default)))` -- the one-liner every
    `_format_event` branch below uses to pull a value out of an event's
    (untrusted) `fields` dict, so there is no path from `fields` to a
    widget that skips `sanitize()`."""
    return sanitize(str(fields.get(key, default)))


def _load_tokens() -> dict[str, Any]:
    """Read `design/tokens.json` fresh from disk. FAILS LOUDLY
    (`FileNotFoundError` naming the resolved path) if it's missing --
    the design constitution is the single source of truth for color, so
    a TUI that can't reach it must not silently invent its own palette.
    """
    if not _TOKENS_PATH.exists():
        raise FileNotFoundError(
            f"design/tokens.json not found at {_TOKENS_PATH} -- the TUI's "
            "colors come from the design constitution "
            "(docs/superpowers/specs/2026-08-04-smac-design-system.md), "
            "not hardcoded hex. Run from within the smac repo checkout."
        )
    return dict(json.loads(_TOKENS_PATH.read_text(encoding="utf-8")))


@functools.lru_cache(maxsize=1)
def _dark_palette() -> dict[str, str]:
    """Every `color.*` token's dark value, keyed by token name (e.g.
    `"accent" -> "#818CF8"`, `"agent" -> "#A78BFA"`,
    `"text-dim" -> "#8B8B93"`) -- the TUI is a dark-terminal surface, so
    it always reads the `dark` half of each token. Cached (one file
    read for the process's whole life; `lru_cache` on a zero-arg
    function is the simplest memoization that still calls `_load_tokens`
    -- and so still fails loudly -- on first use rather than at import
    time, so importing this module never requires the repo layout to be
    correct, only actually building an `AgentApp` does.
    """
    tokens = _load_tokens()
    return {name: value["dark"] for name, value in tokens["color"].items()}


class AgentLike(Protocol):
    """The subset of `Agent`'s surface `AgentApp` depends on, as a
    Protocol -- so tests can inject a plain fake with no `SmacLink`/
    `Brain`/`Guard` behind it, the same rationale as `agent.py`'s own
    `SmacLinkLike`/`BrainLike`."""

    handle: str
    paused: bool
    link: Any  # `.link.credentials.workspace_name` -- see `_workspace_name`

    async def chat(self, text: str) -> str: ...
    async def run(self) -> None: ...


def _workspace_name(agent: AgentLike) -> str:
    """`agent.link.credentials.workspace_name` if reachable, else a
    plain fallback -- read through public attributes only (`Agent.link`
    is public; `Agent._workspace_name` is deliberately private, "internal
    wiring for the guard/persona only" per `agent.py`'s docstring, so the
    header does not reach for it)."""
    credentials = getattr(getattr(agent, "link", None), "credentials", None)
    name = getattr(credentials, "workspace_name", None)
    return name if isinstance(name, str) and name else "workspace"


def _ts(event: Event) -> str:
    return event.at.strftime("%H:%M:%S")


def _format_event(event: Event, palette: dict[str, str]) -> Text | None:
    """One inner/chat-pane line for `event`, or `None` to render nothing
    (`token` events -- streamed deltas are collapsed into the
    `model_call`/`model_done` summary lines, per the design doc's "LLM
    calls collapsed to a summary line").

    Every value pulled from `event.fields` goes through `_field()`
    (-> `sanitize()`) before it reaches `Text.append()` -- this is the
    single choke point named in the module docstring's SECURITY
    section. `Text.append(str)` on its own only guarantees inertness
    against Rich/Textual MARKUP; `sanitize()` is what neutralizes raw
    control bytes (ESC included) and redacts key-shaped tokens.
    """
    if event.kind == "token":
        return None

    dim = palette["text-dim"]
    agent_c = palette["agent"]
    accent = palette["accent"]
    success = palette["success"]
    danger = palette["danger"]
    bell = palette["bell"]

    fields = event.fields
    line = Text()
    line.append(_ts(event), style=dim)
    line.append("  ")

    kind = event.kind
    if kind == "mention":
        line.append("● mention ", style=f"bold {accent}")
        line.append(f"@{_field(fields, 'sender')} #{_field(fields, 'channel')}")
        text = fields.get("text")
        if text:
            line.append(f"  {sanitize(str(text))}")
    elif kind == "context":
        line.append("    context ", style=dim)
        line.append(f"{_field(fields, 'count')} messages")
    elif kind == "model_call":
        line.append("▸ model   ", style=f"bold {agent_c}")
        line.append(_field(fields, "model"))
    elif kind == "model_done":
        seconds = fields.get("seconds")
        secs_text = f"{seconds:.1f}s" if isinstance(seconds, (int, float)) else "?s"
        line.append("✓ done    ", style=f"bold {agent_c}")
        line.append(
            f"{_field(fields, 'input_tokens')}/{_field(fields, 'output_tokens')} "
            f"tok · {secs_text}"
        )
    elif kind == "posted":
        line.append("→ posted  ", style=f"bold {success}")
        line.append(f"#{_field(fields, 'channel')}: {_field(fields, 'text', '')}")
    elif kind == "acked":
        line.append("  acked   ", style=dim)
        line.append(_field(fields, "mention_id", ""))
    elif kind == "skipped":
        line.append("⊘ skipped ", style=dim)
        line.append(_field(fields, "reason", ""))
    elif kind == "paused_skip":
        line.append("⏸ paused  ", style=dim)
        line.append(f"mention {_field(fields, 'mention_id', '')} skipped")
    elif kind == "disconnected":
        line.append("✕ disconnected ", style=f"bold {danger}")
        line.append(_field(fields, "reason", ""))
    elif kind == "reconnected":
        line.append("✓ reconnected", style=f"bold {success}")
    elif kind == "error":
        line.append("! error    ", style=f"bold {danger}")
        line.append(_field(fields, "message", ""))
    elif kind == "chat_in":
        line.append("you › ", style=f"bold {accent}")
        line.append(_field(fields, "text", ""))
    elif kind == "chat_out":
        line.append("agent › ", style=f"bold {agent_c}")
        line.append(_field(fields, "text", ""))
    else:
        # Forward-compatible: an event kind this module doesn't know
        # about yet is still shown (kind + whatever fields it carries),
        # never silently dropped.
        line.append(kind, style=f"bold {bell}")
        for key, value in fields.items():
            line.append(f"  {key}={sanitize(str(value))}")

    return line


class AgentApp(App[None]):
    """The two-pane TUI. `agent`/`bus` are the same instances `main.py`
    wires up for every other mode (`--headless`, `--chat-only`) -- this
    app is just one more view onto them, per the design doc's "three
    views onto one brain."
    """

    CSS_PATH = "tui.tcss"
    TITLE = "analyst_agent"

    BINDINGS = [
        Binding("f2", "inner_only", "Inner", show=True),
        Binding("f3", "chat_only", "Chat", show=True),
        Binding("f4", "toggle_pause", "Pause", show=True),
        Binding("f10", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    def __init__(self, agent: AgentLike, bus: Bus) -> None:
        # Set before `super().__init__()`: `App.__init__` itself calls
        # `self.get_css_variables()` to build the initial stylesheet, so
        # `_palette` must already exist by then.
        self._palette = _dark_palette()
        super().__init__()
        self.agent = agent
        self.bus = bus
        self._view_mode = "both"  # "both" | "inner" | "chat"
        self._connection_state = "connected"
        self._follow_task: asyncio.Task[None] | None = None
        self._mention_task: asyncio.Task[None] | None = None
        self._chat_tasks: set[asyncio.Task[None]] = set()

    # -- theming: tokens.json -> Textual CSS variables ---------------------

    def get_css_variables(self) -> dict[str, str]:
        """Merge `design/tokens.json`'s dark palette into Textual's CSS
        variables as `$smac-<token>` (e.g. `$smac-accent`) -- namespaced
        so this never shadows Textual's own built-in `$accent`/`$surface`/
        etc. `tui.tcss` references only `$smac-*` names, so every color
        on screen traces back to the constitution file, not a literal
        hex value anywhere in this module or the stylesheet."""
        variables = dict(super().get_css_variables())
        for name, value in self._palette.items():
            variables[f"smac-{name}"] = value
        return variables

    # -- layout --------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="header-bar", markup=False)
        with Horizontal(id="panes"):
            yield RichLog(
                id="inner", markup=False, highlight=False, wrap=True, auto_scroll=True
            )
            yield RichLog(
                id="chat", markup=False, highlight=False, wrap=True, auto_scroll=True
            )
        yield Input(id="chat-input", placeholder="talk to the agent…")
        yield Footer()

    # -- lifecycle -------------------------------------------------------

    async def on_mount(self) -> None:
        self._render_header()
        for event in self.bus.history(_HISTORY_SEED):
            self._apply_event(event)

        self._follow_task = asyncio.create_task(
            self._follow_bus(), name="tui-bus-follow"
        )
        self._mention_task = asyncio.create_task(
            self.agent.run(), name="tui-mention-loop"
        )
        # One scheduling turn so both tasks reach their first `await`
        # (the bus subscriber registers its queue; the mention loop
        # reaches its own first suspension point) before a caller can
        # publish an event this app would otherwise miss -- the same
        # warm-up idiom `main.py`'s `_run_headless` uses for its printer.
        await asyncio.sleep(0)

        self.query_one("#chat-input", Input).focus()

    async def on_unmount(self) -> None:
        """Cancel both background tasks and await them -- so a quit
        (`f10`/`ctrl+c`, or `run_test()`'s own teardown) never leaves a
        hung task behind. Cancel both before awaiting either, so neither
        gets to run a full extra cycle while the other is still being
        torn down."""
        tasks = [t for t in (self._follow_task, self._mention_task) if t is not None]
        tasks.extend(self._chat_tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _follow_bus(self) -> None:
        async for event in self.bus.subscribe():
            self._apply_event(event)

    async def _run_chat(self, text: str) -> None:
        """Runs one footer submission's `agent.chat(text)`. Unlike the
        mention path (`agent.py`'s `_safe_handle`), `Agent.chat()` has
        no exception handling of its own -- a `BrainError` (or anything
        else `brain.think()` can raise) would otherwise propagate out of
        the bare `asyncio.create_task(self.agent.chat(text))` this
        replaces, becoming nothing but an "exception was never
        retrieved" warning nobody sees. Wrapping it here and publishing
        an `error` bus event mirrors `_safe_handle`'s behavior exactly:
        `_follow_bus` renders that event into `#inner` the same way a
        mention-loop failure already does, so a failed chat is visible
        instead of silent."""
        try:
            await self.agent.chat(text)
        except Exception as exc:  # noqa: BLE001 - mirrors agent.py's `_safe_handle`
            self.bus.publish("error", message=str(exc))

    # -- rendering -------------------------------------------------------

    def _apply_event(self, event: Event) -> None:
        """Route one event to its pane, updating the header's connection
        state first if this event carries one -- so a `disconnected`/
        `reconnected` event both logs a trace line AND flips the header's
        `SMAC ● <state>` indicator, whether it came from `bus.history()`
        seeding or the live subscription."""
        if event.kind == "disconnected":
            self._connection_state = "disconnected"
            self._render_header()
        elif event.kind == "reconnected":
            self._connection_state = "connected"
            self._render_header()

        line = _format_event(event, self._palette)
        if line is None:
            return
        target_id = "chat" if event.kind in ("chat_in", "chat_out") else "inner"
        self.query_one(f"#{target_id}", RichLog).write(line)

    def _render_header(self) -> None:
        """`handle`/`workspace` come from the server's join response
        (`SmacLink`'s credentials) -- untrusted if `SMAC_URL` points
        somewhere hostile, so both go through `sanitize()` before
        `Static.update()`, same as every SMAC-sourced field in
        `_format_event`."""
        header = self.query_one("#header-bar", Static)
        handle = sanitize(str(getattr(self.agent, "handle", "agent")))
        workspace = sanitize(_workspace_name(self.agent))
        state = self._connection_state

        line = Text()
        line.append("analyst_agent", style="bold")
        line.append("  ·  @")
        line.append(handle)
        line.append("  ·  ")
        line.append(workspace)
        line.append("      SMAC ")
        dot_style = (
            self._palette["success"]
            if state == "connected"
            else self._palette["danger"]
        )
        line.append("● ", style=dot_style)
        line.append(state)
        if getattr(self.agent, "paused", False):
            line.append("   ⏸ PAUSED", style=f"bold {self._palette['bell']}")
        header.update(line)

    # -- input: footer talks to the agent, never to SMAC ------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "chat-input":
            return
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        # `agent.chat()` publishes `chat_in`/`chat_out` on the bus itself
        # (see `Agent.chat`'s docstring) -- `_follow_bus` renders the
        # exchange into `#chat` from those events, so this handler never
        # writes to the chat pane directly. Spawned as a task (not
        # awaited here) so a slow model call never blocks the input
        # widget from accepting the next keystroke; wrapped in
        # `_run_chat` so a failure surfaces as an `error` bus event
        # instead of an unretrieved-task warning (see `_run_chat`'s
        # docstring). Kept in `self._chat_tasks` (not just handed to
        # `create_task` and dropped) so nothing garbage-collects it
        # mid-flight, and `on_unmount` cancels-and-awaits it like every
        # other background task this app owns.
        task = asyncio.create_task(self._run_chat(text), name="tui-chat")
        self._chat_tasks.add(task)
        task.add_done_callback(self._chat_tasks.discard)

    # -- bindings ----------------------------------------------------------

    def action_inner_only(self) -> None:
        self._view_mode = "both" if self._view_mode == "inner" else "inner"
        self._apply_view_mode()

    def action_chat_only(self) -> None:
        self._view_mode = "both" if self._view_mode == "chat" else "chat"
        self._apply_view_mode()

    def action_toggle_pause(self) -> None:
        self.agent.paused = not self.agent.paused
        self._render_header()

    def _apply_view_mode(self) -> None:
        inner = self.query_one("#inner", RichLog)
        chat = self.query_one("#chat", RichLog)
        inner.display = self._view_mode != "chat"
        chat.display = self._view_mode != "inner"


def run_tui(agent: AgentLike, bus: Bus) -> None:
    """`main.py`'s seam target -- build and run the app, blocking until
    quit. `App.run()` manages its own asyncio event loop; `AgentApp`
    starts `agent.run()` (the mention loop) and the bus-follow task
    inside it (see `on_mount`), so this one call is the entire
    non-headless entry point."""
    AgentApp(agent, bus).run()
