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

import pytest

import analyst_agent.main as main_module
from analyst_agent.smac_link import JoinFailed


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "load_dotenv", lambda: None)


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
