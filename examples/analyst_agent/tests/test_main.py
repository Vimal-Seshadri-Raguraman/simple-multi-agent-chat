"""Tests for `main.py`'s CLI contract: a `ConfigError` or `JoinFailed`
prints the error's own message to stderr and exits 2 -- never a raw
traceback -- and the TUI branch is a real lazy seam (Task 5, not built
yet), not a stub that pretends to work.

`load_dotenv` is stubbed out in every test here (`no_dotenv` autouse
fixture): `main()` calling the real one would merge whatever `.env`
happens to exist at this checkout's repo root into `os.environ`, which
has nothing to do with what these tests are isolating.
"""

from __future__ import annotations

import io
import sys
from typing import Any

import pytest

import analyst_agent.main as main_module
from analyst_agent.agent import Agent
from analyst_agent.brain import BrainError, Reply
from analyst_agent.bus import Bus
from analyst_agent.guard import Guard
from analyst_agent.smac_link import Credentials, JoinFailed


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def test_missing_anthropic_api_key_exits_2_with_configs_own_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    code = main_module.main([])

    assert code == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in err
    assert "Traceback" not in err


def test_join_failed_exits_2_with_the_servers_own_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("SMAC_AGENT_CODE", raising=False)

    def fake_join_or_load(self: object) -> None:
        raise JoinFailed(
            "Invite is invalid or expired -- mint a fresh one in Settings → Invites"
        )

    monkeypatch.setattr(
        "analyst_agent.smac_link.SmacLink.join_or_load", fake_join_or_load
    )

    code = main_module.main([])

    assert code == 2
    err = capsys.readouterr().err
    assert "Invite is invalid or expired" in err
    assert "Traceback" not in err


def test_run_tui_is_a_lazy_seam_that_delegates_to_the_tui_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `run_tui` imports `analyst_agent.tui` lazily (so every non-TUI mode
    # never needs `tui.py`'s imports exercised) and then delegates
    # straight through -- verified here without actually launching a
    # terminal app, by monkeypatching the real target.
    import analyst_agent.tui as tui_module

    calls: list[tuple[object, object]] = []
    monkeypatch.setattr(
        tui_module, "run_tui", lambda agent, bus: calls.append((agent, bus))
    )

    main_module.run_tui(agent="AGENT", bus="BUS")  # type: ignore[arg-type]

    assert calls == [("AGENT", "BUS")]


def test_parses_headless_chat_only_and_once_flags() -> None:
    args = main_module._parse_args(["--headless", "--chat-only", "--once"])
    assert args.headless and args.chat_only and args.once

    defaults = main_module._parse_args([])
    assert not (defaults.headless or defaults.chat_only or defaults.once)


# -- F3+F4: main.py's own print sites go through sanitize() -----------------
#
# `tui.py`'s `sanitize()` (now `sanitize.py`) was, before this fix wave,
# wired into every widget in `tui.py` but NOT into `main.py`'s own
# `print()` calls -- which run before any TUI exists, in every mode. These
# tests use real escape payloads (security-review.md's exploit scenarios)
# against those specific call sites.

_OSC_TITLE_SPOOF = "\x1b]0;PWNED\x1b\\"
_CSI_CLEAR_SCREEN = "\x1b[2J\x1b[H"


def test_join_failed_message_is_sanitized_before_printing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A hostile/MITM'd `SMAC_URL` can put arbitrary escape bytes in
    `JoinFailed`'s message (the server's own error body, embedded
    verbatim by `smac_link.py::_server_message`) -- this must never
    reach the operator's real terminal raw."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("SMAC_AGENT_CODE", raising=False)

    def fake_join_or_load(self: object) -> None:
        raise JoinFailed(f"{_OSC_TITLE_SPOOF}Invite is invalid{_CSI_CLEAR_SCREEN}")

    monkeypatch.setattr(
        "analyst_agent.smac_link.SmacLink.join_or_load", fake_join_or_load
    )

    code = main_module.main([])

    assert code == 2
    err = capsys.readouterr().err
    assert "\x1b" not in err  # the raw ESC byte never lands on the terminal
    assert "Invite is invalid" in err  # the message itself still shows
    assert "Traceback" not in err


def test_config_error_message_is_sanitized_before_printing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Defense in depth (final-review.md F4): even though today's
    `ConfigError` messages are operator-self-inflicted (their own
    `.env`), this print rides the same choke point as every other
    terminal print in this module, for the invariant's sake."""

    def fake_load_config(env: object) -> object:
        raise main_module.ConfigError(f"bad value{_OSC_TITLE_SPOOF} for MAX_HOPS")

    monkeypatch.setattr(main_module, "load_config", fake_load_config)

    code = main_module.main([])

    assert code == 2
    err = capsys.readouterr().err
    assert "\x1b" not in err
    assert "bad value" in err


# -- F3: the --chat-only REPL's own print sites ------------------------------


class _ReplLink:
    """Minimal `SmacLinkLike` double -- `_run_chat_repl`/`agent.chat()`
    never call any of these, they only exist to satisfy `Agent.__init__`
    (which reads `link.credentials` once at construction)."""

    def __init__(self, credentials: Credentials) -> None:
        self.credentials: Credentials | None = credentials

    def history(self, channel_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return []

    def post(self, channel_id: str, text: str) -> dict[str, Any]:
        return {}

    def pending_mentions(self) -> list[dict[str, Any]]:
        return []

    def ack(self, mention_id: str) -> None:
        return None

    async def events(self):  # pragma: no cover - never driven by these tests
        return
        yield  # satisfies the AsyncIterator shape


class _ReplBrain:
    """Minimal `BrainLike` double: answers with a fixed reply, or raises
    `BrainError` (mirroring what `Brain.think()` does on a real Anthropic
    failure) when `raise_error=True`."""

    def __init__(self, bus: Bus, reply_text: str, *, raise_error: bool = False) -> None:
        self.bus = bus
        self.reply_text = reply_text
        self.raise_error = raise_error

    async def think(
        self,
        system: str,
        history: list[dict[str, str]],
        trigger: str,
        thread: list[dict[str, str]] | None = None,
    ) -> Reply:
        if self.raise_error:
            self.bus.publish("error", message="boom")
            raise BrainError("boom")
        return Reply(text=self.reply_text, input_tokens=0, output_tokens=0, seconds=0.0)


def _repl_agent(
    cfg: Any,
    *,
    handle: str = "analyst",
    reply_text: str = "hi",
    raise_error: bool = False,
) -> Agent:
    credentials = Credentials(
        member_id="mem-1",
        handle=handle,
        api_key="smac-secret-key-xyz",
        workspace_id="ws-1",
        workspace_name="Test Workspace",
    )
    bus = Bus()
    link = _ReplLink(credentials)
    brain = _ReplBrain(bus, reply_text, raise_error=raise_error)
    guard = Guard(max_replies_per_min=6, max_hops=3)
    return Agent(link, brain, guard, bus, cfg)


@pytest.mark.anyio
async def test_chat_repl_sanitizes_the_handle_banner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cfg: Any
) -> None:
    """`agent.handle` comes from the server's join response -- untrusted
    if `SMAC_URL` points somewhere hostile (security-review.md Vuln 2)."""
    agent = _repl_agent(cfg, handle=f"{_OSC_TITLE_SPOOF}analyst")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # immediate EOF

    await main_module._run_chat_repl(agent)

    err = capsys.readouterr().err
    assert "\x1b" not in err
    assert "analyst" in err


@pytest.mark.anyio
async def test_chat_repl_sanitizes_the_models_reply(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cfg: Any
) -> None:
    """No hostile server needed for this one: an ordinary workspace
    member can post control bytes as plain text, and the model can echo
    them back verbatim in a reply -- the exact no-hostile-server exploit
    security-review.md's Vuln 2 describes."""
    reply_text = f"{_CSI_CLEAR_SCREEN}here's what alice said"
    agent = _repl_agent(cfg, reply_text=reply_text)
    monkeypatch.setattr(sys, "stdin", io.StringIO("what did alice say?\n"))

    await main_module._run_chat_repl(agent)

    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "here's what alice said" in out


@pytest.mark.anyio
async def test_chat_repl_reports_a_brain_error_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cfg: Any
) -> None:
    agent = _repl_agent(cfg, raise_error=True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello\n"))

    await main_module._run_chat_repl(agent)

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err and "Traceback" not in captured.out
    assert "boom" in captured.err
