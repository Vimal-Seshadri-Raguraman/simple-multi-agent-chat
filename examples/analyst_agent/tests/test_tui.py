"""Tests for `tui.py`'s `AgentApp` -- the two-pane Textual TUI (Task 5).

`FakeAgent`/`FakeLink`/`FakeCredentials` below are plain duck-typed
doubles matching `AgentLike`'s Protocol -- no real `SmacLink`/`Brain`/
`Guard`, no network, no `ANTHROPIC_API_KEY`. `FakeAgent.chat()` mirrors
the real `Agent.chat()`'s observable bus footprint (publishes `chat_in`
then `chat_out`) so the chat pane's bus-driven rendering is exercised the
same way it would be against the real agent. `FakeAgent.run()` awaits
forever (like the real mention loop) so `on_unmount`'s cancel-and-await
is exercised by every single test here -- if that discipline were broken
(a hung task on quit), every test in this module would hang or leak a
"Task was destroyed but it is pending" warning, not just one dedicated
case.

`inner_text`/`chat_text`/`header_text` read back what's actually on
screen via public widget APIs (`RichLog.lines` -- `Strip.text` per
visible line; `Static.content` -- the `Text` last passed to `.update()`)
so these tests assert what a human looking at the terminal would see,
not internal state this module happens to keep.
"""

from __future__ import annotations

import asyncio
import secrets as secrets_module
from typing import Any

import pytest
from textual.widgets import RichLog, Static

from analyst_agent.bus import Bus
from analyst_agent.tui import AgentApp

#: Real escape payloads (design doc / security review finding (a)):
#: - OSC title-bar spoof: sets the terminal window/tab title.
#: - CSI clear-screen + cursor-home: wipes and repositions the viewport.
#: - OSC52 clipboard write (shape only -- base64 payload is irrelevant to
#:   the assertion, only the leading ESC matters).
_OSC_TITLE_SPOOF = "\x1b]0;PWNED\x1b\\"
_CSI_CLEAR_SCREEN = "\x1b[2J\x1b[H"
_OSC52_CLIPBOARD = "\x1b]52;c;UFdORUQ=\x07"


class FakeCredentials:
    workspace_name = "Acme Rockets"


class FakeLink:
    def __init__(self) -> None:
        self.credentials = FakeCredentials()
        self.posted: list[tuple[str, str]] = []

    def post(self, channel_id: str, text: str) -> dict[str, Any]:
        self.posted.append((channel_id, text))
        return {}


class FakeAgent:
    def __init__(self, bus: Bus) -> None:
        self.bus = bus
        self.link = FakeLink()
        self.handle = "analyst"
        self.paused = False
        self.chat_calls: list[str] = []

    async def chat(self, text: str) -> str:
        self.chat_calls.append(text)
        self.bus.publish("chat_in", text=text)
        reply = f"you said: {text}"
        self.bus.publish("chat_out", text=reply)
        return reply

    async def run(self) -> None:
        # Mirrors the real mention loop's shape: runs forever until the
        # app cancels it on quit.
        await asyncio.Event().wait()


class FailingChatAgent(FakeAgent):
    """Like `FakeAgent`, but `chat()` raises instead of replying --
    mirrors a `BrainError` propagating out of `Agent.chat()`, which (per
    finding (b)) has no `_safe_handle` of its own to catch it. Still
    publishes `chat_in` first, exactly like the real `Agent.chat()`
    (see its docstring: `chat_in` is published before `brain.think()`
    is ever called), so this exercises the "the request went out, then
    failed" ordering, not "nothing happened at all"."""

    async def chat(self, text: str) -> str:
        self.chat_calls.append(text)
        self.bus.publish("chat_in", text=text)
        raise RuntimeError("brain exploded: model overloaded")


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
def bus() -> Bus:
    return Bus()


@pytest.fixture()
def agent(bus: Bus) -> FakeAgent:
    return FakeAgent(bus)


def inner_text(pilot: Any) -> str:
    log = pilot.app.query_one("#inner", RichLog)
    return "\n".join(strip.text for strip in log.lines)


def chat_text(pilot: Any) -> str:
    log = pilot.app.query_one("#chat", RichLog)
    return "\n".join(strip.text for strip in log.lines)


def header_text(pilot: Any) -> str:
    header = pilot.app.query_one("#header-bar", Static)
    return str(header.content)


# -- _known_secrets_for(): wiring the two runtime secrets into the TUI ------
#
# `run_tui()` is the seam that pulls `agent.link.credentials.api_key` /
# `agent.config.anthropic_api_key` off a real `Agent` and hands them to
# `AgentApp(secrets=...)`. These are plain-function tests (no Textual
# pilot needed) against doubles that only carry the attributes being
# read, so they exercise the `getattr`-chain fallbacks directly.


def test_known_secrets_for_pulls_both_keys_off_a_fully_wired_agent() -> None:
    from analyst_agent.tui import _known_secrets_for

    class _Credentials:
        api_key = "smac-runtime-key-value"

    class _Link:
        credentials = _Credentials()

    class _Config:
        anthropic_api_key = "sk-ant-runtime-key-value"

    class _WiredAgent:
        link = _Link()
        config = _Config()

    result = _known_secrets_for(_WiredAgent())  # type: ignore[arg-type]
    assert result == {"smac-runtime-key-value", "sk-ant-runtime-key-value"}


def test_known_secrets_for_tolerates_an_agent_missing_both_attributes() -> None:
    from analyst_agent.tui import _known_secrets_for

    class _BareAgent:
        pass

    assert _known_secrets_for(_BareAgent()) == frozenset()  # type: ignore[arg-type]


# -- inner pane: event stream + security -----------------------------------


@pytest.mark.anyio
async def test_inner_pane_renders_the_event_stream(agent: FakeAgent, bus: Bus) -> None:
    async with AgentApp(agent, bus).run_test() as pilot:
        bus.publish("mention", sender="Alice", channel="general", text="hi")
        await pilot.pause()

        text = inner_text(pilot)
        assert "mention" in text
        assert "Alice" in text
        assert "general" in text


@pytest.mark.anyio
async def test_message_markup_renders_inert(agent: FakeAgent, bus: Bus) -> None:
    async with AgentApp(agent, bus).run_test() as pilot:
        bus.publish(
            "mention", sender="Alice", channel="general", text="[bold red]PWNED[/]"
        )
        await pilot.pause()

        text = inner_text(pilot)
        assert "[bold red]PWNED[/]" in text  # literal, never styled/parsed


@pytest.mark.anyio
async def test_sender_name_markup_also_renders_inert(
    agent: FakeAgent, bus: Bus
) -> None:
    async with AgentApp(agent, bus).run_test() as pilot:
        bus.publish("mention", sender="[bold]Mallory[/]", channel="general")
        await pilot.pause()

        assert "[bold]Mallory[/]" in inner_text(pilot)


# -- ANSI escape injection (finding (a)) ------------------------------------
#
# `Text.append()` + `markup=False` only neutralize Rich/Textual MARKUP.
# They do nothing about raw ESC bytes: `rich.control.STRIP_CONTROL_CODES`
# is [7, 8, 11, 12, 13] -- ESC (0x1b) is not in that list -- and Textual's
# `Strip.render_style()` embeds segment text raw into the ANSI SGR bytes
# it writes to the terminal fd. These tests assert the actual defense
# (`sanitize()`) does its job: the ESC byte itself must never survive
# into what a `RichLog`/`Static` renders, in both a message body and a
# sender name.


@pytest.mark.anyio
async def test_escape_payloads_never_survive_in_a_message_body(
    agent: FakeAgent, bus: Bus
) -> None:
    async with AgentApp(agent, bus).run_test() as pilot:
        bus.publish(
            "mention",
            sender="Alice",
            channel="general",
            text=_OSC_TITLE_SPOOF + _CSI_CLEAR_SCREEN + _OSC52_CLIPBOARD,
        )
        await pilot.pause()

        text = inner_text(pilot)
        assert "\x1b" not in text  # the byte that actually reaches the terminal
        # Sanitized-but-visible, not silently vanished:
        assert "\\x1b" in text


@pytest.mark.anyio
async def test_escape_payloads_never_survive_in_a_sender_name(
    agent: FakeAgent, bus: Bus
) -> None:
    async with AgentApp(agent, bus).run_test() as pilot:
        bus.publish(
            "mention",
            sender=_OSC_TITLE_SPOOF + "Mallory" + _CSI_CLEAR_SCREEN,
            channel="general",
        )
        await pilot.pause()

        text = inner_text(pilot)
        assert "\x1b" not in text
        assert "Mallory" in text  # the harmless part of the name still shows


@pytest.mark.anyio
async def test_escape_payload_never_survives_in_the_header_handle_or_workspace(
    bus: Bus,
) -> None:
    agent = FakeAgent(bus)
    agent.handle = "analyst" + _CSI_CLEAR_SCREEN
    agent.link.credentials.workspace_name = _OSC_TITLE_SPOOF + "Acme"

    async with AgentApp(agent, bus).run_test() as pilot:
        text = header_text(pilot)
        assert "\x1b" not in text
        assert "analyst" in text
        assert "Acme" in text


@pytest.mark.anyio
async def test_llm_calls_collapse_to_a_summary_no_per_token_lines(
    agent: FakeAgent, bus: Bus
) -> None:
    async with AgentApp(agent, bus).run_test() as pilot:
        bus.publish("model_call", model="claude-sonnet-5", temp=1.0, context_size=42)
        bus.publish("token", text="Hello")
        bus.publish("token", text=" world")
        bus.publish("model_done", input_tokens=10, output_tokens=5, seconds=1.25)
        await pilot.pause()

        text = inner_text(pilot)
        assert "claude-sonnet-5" in text
        assert "10/5" in text and "1.2" in text
        assert "Hello" not in text and "world" not in text  # tokens never get a line


@pytest.mark.anyio
async def test_on_mount_seeds_the_inner_pane_from_bus_history(bus: Bus) -> None:
    # Publish BEFORE the app (and its live subscription) exists -- only
    # `bus.history()` seeding can be responsible for this showing up.
    bus.publish("mention", sender="Bob", channel="ops", text="ping")
    agent = FakeAgent(bus)

    async with AgentApp(agent, bus).run_test() as pilot:
        await pilot.pause()
        text = inner_text(pilot)
        assert "mention" in text and "Bob" in text


# -- chat pane / footer input --------------------------------------------


@pytest.mark.anyio
async def test_typing_in_the_footer_talks_to_the_agent_not_smac(
    agent: FakeAgent, bus: Bus
) -> None:
    async with AgentApp(agent, bus).run_test() as pilot:
        await pilot.press(*"hello", "enter")
        await pilot.pause()

        assert agent.chat_calls == ["hello"]
        assert agent.link.posted == []


@pytest.mark.anyio
async def test_chat_exchange_renders_in_the_chat_pane_not_inner(
    agent: FakeAgent, bus: Bus
) -> None:
    async with AgentApp(agent, bus).run_test() as pilot:
        await pilot.press(*"hello", "enter")
        await pilot.pause()

        chat = chat_text(pilot)
        assert "hello" in chat and "you said: hello" in chat
        assert "hello" not in inner_text(pilot)


@pytest.mark.anyio
async def test_a_failed_chat_surfaces_as_an_error_instead_of_vanishing(
    bus: Bus,
) -> None:
    """Finding (b): `Agent.chat()` has no `_safe_handle` of its own (see
    `agent.py`), so a bare `asyncio.create_task(self.agent.chat(text))`
    would turn a `BrainError` into an unretrieved-task warning nobody
    sees. `_run_chat` must catch it and publish an `error` bus event,
    the same way the mention loop's `_safe_handle` already does -- and
    that event must actually render, in `#inner`."""
    agent = FailingChatAgent(bus)

    async with AgentApp(agent, bus).run_test() as pilot:
        await pilot.press(*"hello", "enter")
        await pilot.pause()

        assert agent.chat_calls == ["hello"]
        text = inner_text(pilot)
        assert "error" in text
        assert "brain exploded" in text


# -- f4: pause -------------------------------------------------------------


@pytest.mark.anyio
async def test_f4_toggles_pause_and_shows_it(agent: FakeAgent, bus: Bus) -> None:
    async with AgentApp(agent, bus).run_test() as pilot:
        assert "PAUSED" not in header_text(pilot)

        await pilot.press("f4")
        await pilot.pause()

        assert agent.paused is True
        assert "PAUSED" in header_text(pilot)

        await pilot.press("f4")
        await pilot.pause()

        assert agent.paused is False
        assert "PAUSED" not in header_text(pilot)


# -- f2 / f3: view modes -----------------------------------------------


@pytest.mark.anyio
async def test_f2_and_f3_switch_focus_modes(agent: FakeAgent, bus: Bus) -> None:
    async with AgentApp(agent, bus).run_test() as pilot:
        inner = pilot.app.query_one("#inner")
        chat = pilot.app.query_one("#chat")
        assert inner.display and chat.display  # both visible by default

        await pilot.press("f2")
        await pilot.pause()
        assert inner.display and not chat.display  # inner-only

        await pilot.press("f2")  # toggle back
        await pilot.pause()
        assert inner.display and chat.display

        await pilot.press("f3")
        await pilot.pause()
        assert chat.display and not inner.display  # chat-only

        await pilot.press("f3")  # toggle back
        await pilot.pause()
        assert inner.display and chat.display


# -- header --------------------------------------------------------------


@pytest.mark.anyio
async def test_header_shows_handle_workspace_and_connection_state(
    agent: FakeAgent, bus: Bus
) -> None:
    async with AgentApp(agent, bus).run_test() as pilot:
        text = header_text(pilot)
        assert "analyst" in text
        assert "Acme Rockets" in text
        assert "connected" in text


@pytest.mark.anyio
async def test_header_reflects_disconnected_and_reconnected(
    agent: FakeAgent, bus: Bus
) -> None:
    async with AgentApp(agent, bus).run_test() as pilot:
        bus.publish("disconnected", reason="connection closed")
        await pilot.pause()
        assert "disconnected" in header_text(pilot)

        bus.publish("reconnected")
        await pilot.pause()
        assert "connected" in header_text(pilot)
        assert "disconnected" not in header_text(pilot)


# -- no secrets, no hung tasks -------------------------------------------


@pytest.mark.anyio
async def test_key_shaped_token_in_a_bus_event_is_redacted_not_rendered(
    agent: FakeAgent, bus: Bus
) -> None:
    """Finding (c): the original version of this test set
    `credentials.api_key`, an attribute `tui.py` never reads anywhere --
    it would pass against any implementation, including one with no
    redaction at all. The realistic leak vector is a secret-looking
    value inside a bus event's `fields` (e.g. an SDK exception message
    an `error` event carries via `agent.py`'s `_safe_handle`/`brain.py`'s
    `BrainError`). Before `sanitize()` gained `_SECRET_TOKEN` redaction,
    `_format_event`'s `error` branch rendered `fields['message']`
    unmodified -- this WOULD have rendered the key verbatim, confirming
    the finding was real, not hypothetical."""
    async with AgentApp(agent, bus).run_test() as pilot:
        bus.publish("error", message="auth failed for key sk-ant-SECRET-value-123")
        await pilot.pause()

        rendered = inner_text(pilot) + chat_text(pilot) + header_text(pilot)
        assert "sk-ant-SECRET-value-123" not in rendered
        assert "[REDACTED]" in rendered
        assert "error" in rendered  # the event itself still shows, just redacted


# -- known-value secret redaction (fix-round finding) -----------------------
#
# The `smac-` prefix branch in `_SECRET_TOKEN` matched nothing real: SMAC
# API keys (`app/auth.py`'s `generate_api_key()`) are a bare
# `secrets.token_urlsafe(32)` string with NO prefix. A leaked SMAC key
# rendered completely unredacted despite the module docstring's claim
# that "this project's own SMAC keys" were covered. The fix redacts by
# VALUE instead: `AgentApp(..., secrets={...})` registers the exact
# secret values this process holds, and `sanitize()` replaces any exact
# occurrence of one of them. These tests exercise that new mechanism and
# its documented boundaries directly -- they fail against the pre-fix
# code because `AgentApp.__init__` didn't accept a `secrets=` kwarg at
# all.


@pytest.mark.anyio
async def test_a_known_smac_key_value_is_redacted_in_an_error_event(
    agent: FakeAgent, bus: Bus
) -> None:
    """A real-shaped SMAC key -- `secrets.token_urlsafe(32)`, exactly
    what `app/auth.py`'s `generate_api_key()` returns -- registered as a
    known secret via `AgentApp(secrets={...})`, must render redacted
    when it shows up in an `error` event's message. The old `smac-`
    prefix heuristic could never have caught this (no prefix exists);
    only exact-value redaction can."""
    key = secrets_module.token_urlsafe(32)

    async with AgentApp(agent, bus, secrets={key}).run_test() as pilot:
        bus.publish("error", message=f"auth failed for key {key}")
        await pilot.pause()

        rendered = inner_text(pilot) + chat_text(pilot) + header_text(pilot)
        assert key not in rendered
        assert "[REDACTED]" in rendered
        assert "error" in rendered  # the event itself still shows, just redacted


@pytest.mark.anyio
async def test_an_unknown_random_token_renders_as_is_documented_limitation(
    agent: FakeAgent, bus: Bus
) -> None:
    """`sanitize()` makes NO general "any secret gets redacted" promise
    (see its docstring): only known-value redaction (this process's own
    two runtime secrets) and the `sk-ant-` shape heuristic. A random
    token this process never held, in a shape the heuristic doesn't
    match, is NOT redacted -- asserted explicitly here so that boundary
    is documented, verified behavior rather than an unnoticed gap."""
    unknown = secrets_module.token_urlsafe(32)

    async with AgentApp(agent, bus).run_test() as pilot:  # no secrets registered
        bus.publish("error", message=f"unexpected token seen: {unknown}")
        await pilot.pause()

        rendered = inner_text(pilot) + chat_text(pilot) + header_text(pilot)
        assert unknown in rendered
        assert "[REDACTED]" not in rendered


@pytest.mark.anyio
async def test_empty_string_secret_in_the_registry_does_not_corrupt_output(
    agent: FakeAgent, bus: Bus
) -> None:
    """Degenerate-case guard: `str.replace(text, "", "[REDACTED]")`
    would insert the replacement between every character, blanking out
    the entire pane, if an empty (or whitespace-only) "secret" ever
    reached the replace loop unfiltered. `AgentApp` must filter these
    out before `sanitize()` ever sees them -- a plain, unrelated message
    must still render intact even with `""`/whitespace-only entries
    sitting in the registry alongside a real one."""
    async with AgentApp(
        agent, bus, secrets={"", "   ", "some-other-real-secret-xyz"}
    ).run_test() as pilot:
        bus.publish("mention", sender="Alice", channel="general", text="hello world")
        await pilot.pause()

        text = inner_text(pilot)
        assert "hello world" in text  # intact, not shredded per-character
        assert "[REDACTED]" not in text  # the real secret never appeared here


@pytest.mark.anyio
async def test_quits_cleanly_with_a_live_subscription_and_mention_loop_open(
    agent: FakeAgent, bus: Bus
) -> None:
    # The real assertion is implicit: if `on_unmount` didn't cancel both
    # background tasks, `run_test()`'s teardown below would hang (this
    # test -- and every other test in this module -- would time out
    # rather than fail cleanly).
    async with AgentApp(agent, bus).run_test() as pilot:
        bus.publish("mention", sender="Alice", channel="general")
        await pilot.pause()

    assert True
