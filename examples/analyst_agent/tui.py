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
originates from SMAC (message text, sender names) is untrusted input --
rendered with `Text.append()`, which never parses Rich/Textual markup no
matter what characters the string contains, plus `markup=False` on both
`RichLog`s as defense in depth. A message containing `[bold red]PWNED[/]`
must appear on screen as that literal text, not styled. No credential
(SMAC API key, Anthropic key) is ever displayed by this module -- the
header shows only the agent's public handle, workspace name, and
connection state.

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

    Every span built from event data uses `Text.append(str)`, which
    NEVER parses Rich/Textual markup regardless of what the string
    contains -- the security property this whole module exists to
    uphold for SMAC-sourced text (sender names, message text).
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
        line.append(f"@{fields.get('sender', '?')} #{fields.get('channel', '?')}")
        text = fields.get("text")
        if text:
            line.append(f"  {text}")
    elif kind == "context":
        line.append("    context ", style=dim)
        line.append(f"{fields.get('count', '?')} messages")
    elif kind == "model_call":
        line.append("▸ model   ", style=f"bold {agent_c}")
        line.append(f"{fields.get('model', '?')}")
    elif kind == "model_done":
        seconds = fields.get("seconds")
        secs_text = f"{seconds:.1f}s" if isinstance(seconds, (int, float)) else "?s"
        line.append("✓ done    ", style=f"bold {agent_c}")
        line.append(
            f"{fields.get('input_tokens', '?')}/{fields.get('output_tokens', '?')} "
            f"tok · {secs_text}"
        )
    elif kind == "posted":
        line.append("→ posted  ", style=f"bold {success}")
        line.append(f"#{fields.get('channel', '?')}: {fields.get('text', '')}")
    elif kind == "acked":
        line.append("  acked   ", style=dim)
        line.append(f"{fields.get('mention_id', '')}")
    elif kind == "skipped":
        line.append("⊘ skipped ", style=dim)
        line.append(f"{fields.get('reason', '')}")
    elif kind == "paused_skip":
        line.append("⏸ paused  ", style=dim)
        line.append(f"mention {fields.get('mention_id', '')} skipped")
    elif kind == "disconnected":
        line.append("✕ disconnected ", style=f"bold {danger}")
        line.append(f"{fields.get('reason', '')}")
    elif kind == "reconnected":
        line.append("✓ reconnected", style=f"bold {success}")
    elif kind == "error":
        line.append("! error    ", style=f"bold {danger}")
        line.append(f"{fields.get('message', '')}")
    elif kind == "chat_in":
        line.append("you › ", style=f"bold {accent}")
        line.append(f"{fields.get('text', '')}")
    elif kind == "chat_out":
        line.append("agent › ", style=f"bold {agent_c}")
        line.append(f"{fields.get('text', '')}")
    else:
        # Forward-compatible: an event kind this module doesn't know
        # about yet is still shown (kind + whatever fields it carries),
        # never silently dropped.
        line.append(kind, style=f"bold {bell}")
        for key, value in fields.items():
            line.append(f"  {key}={value}")

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
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _follow_bus(self) -> None:
        async for event in self.bus.subscribe():
            self._apply_event(event)

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
        header = self.query_one("#header-bar", Static)
        handle = getattr(self.agent, "handle", "agent")
        workspace = _workspace_name(self.agent)
        state = self._connection_state

        line = Text()
        line.append("analyst_agent", style="bold")
        line.append("  ·  @")
        line.append(str(handle))
        line.append("  ·  ")
        line.append(str(workspace))
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
        # widget from accepting the next keystroke.
        asyncio.create_task(self.agent.chat(text))

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
