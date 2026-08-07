"""CLI entry point for the analyst_agent example (design doc §2/§3).

    python -m analyst_agent.main             # the two-pane Textual app (Task 5)
    python -m analyst_agent.main --headless   # no TUI: one JSON object per bus event, to stdout
    python -m analyst_agent.main --chat-only  # a plain stdin/stdout REPL over agent.chat() -- no mention loop
    python -m analyst_agent.main --once       # handle exactly one mention, then exit (implies headless-style output)

Reads configuration from `.env` (via `load_dotenv()`) merged into the
process environment, then `load_config(os.environ)` -- see `config.py`'s
module docstring for why `load_config` itself never touches `os.environ`
directly (this module is the one place that seam is closed). A
`ConfigError` (bad/missing `.env`) or `JoinFailed` (bad/missing invite
code) prints the error's own message to stderr and exits 2 -- never a
raw traceback; every other exception is left to surface normally, since
those are bugs, not "the user needs to fix their setup."
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from typing import Any

from dotenv import load_dotenv

from analyst_agent.agent import Agent
from analyst_agent.brain import Brain
from analyst_agent.bus import Bus, Event
from analyst_agent.config import ConfigError, load_config
from analyst_agent.guard import Guard
from analyst_agent.smac_link import JoinFailed, SmacLink


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="analyst_agent",
        description="A real, Anthropic-backed example agent that joins a "
        "SMAC workspace as a member and answers when mentioned.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="run without the TUI -- print one JSON object per bus event to stdout",
    )
    parser.add_argument(
        "--chat-only",
        action="store_true",
        help="skip the SMAC mention loop entirely -- a stdin/stdout REPL over "
        "agent.chat() only (never posts to SMAC)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="handle exactly one mention (from the drain or a live frame), then "
        "exit, instead of running forever -- mainly for integration tests",
    )
    return parser.parse_args(argv)


def _event_to_json(event: Event) -> str:
    """One `Event` as one JSON-lines object: `kind`/`at` plus every field,
    flattened -- exactly what `--headless` prints, one per line. `fields`
    is already guaranteed secret-free by every publisher (see `bus.py`'s
    module docstring); `default=str` only exists for stray non-JSON-native
    values (e.g. a `datetime` slipping into `fields`), never a fallback
    for something that shouldn't be there."""
    return json.dumps(
        {"kind": event.kind, "at": event.at.isoformat(), **event.fields}, default=str
    )


async def _headless_printer(bus: Bus) -> None:
    async for event in bus.subscribe():
        print(_event_to_json(event), flush=True)


async def _run_headless(agent: Agent, bus: Bus, *, once: bool) -> None:
    """Subscribe the JSON-lines printer, give it one scheduling turn to
    reach its first `await` (so it can't miss an event `agent.run()`
    publishes in its very first tick -- the same warm-up `bus.py`'s own
    tests use), then run the mention loop. The printer is cancelled once
    `run()` returns; a couple of scheduling turns first give it a chance
    to drain whatever is already sitting in its queue."""
    printer = asyncio.ensure_future(_headless_printer(bus))
    await asyncio.sleep(0)
    try:
        await agent.run(once=once)
    finally:
        for _ in range(3):
            await asyncio.sleep(0)
        printer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await printer


async def _run_chat_repl(agent: Agent) -> None:
    """`--chat-only`: a plain stdin/stdout loop over `agent.chat()`.
    Never touches the mention loop or `link.post`/`link.ack` -- see
    `Agent.chat`'s own docstring for that guarantee. Exits cleanly on
    EOF (stdin closed) or Ctrl-C."""
    loop = asyncio.get_running_loop()
    print(f"Chatting with {agent.handle} -- Ctrl-D to quit.", file=sys.stderr)
    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
        except KeyboardInterrupt:
            return
        if not line:  # EOF
            return
        text = line.rstrip("\n")
        if not text:
            continue
        reply = await agent.chat(text)
        print(f"agent> {reply}")


def run_tui(agent: Agent, bus: Bus) -> None:
    """The default (no flags) experience: the two-pane Textual app
    (`tui.py`'s `AgentApp`) -- header, `#inner` activity stream, `#chat`
    direct-chat pane, footer input. Imported lazily, here, so importing
    `main.py` (and every `--headless`/`--chat-only`/`--once` run) never
    requires `tui.py`'s imports (`textual`, `rich`) to be exercised
    beyond being installed. `AgentApp` itself starts `agent.run()` (the
    mention loop) alongside the TUI's own event loop -- see `tui.py`'s
    module docstring -- so this one call is the entire non-headless
    entry point; nothing else here needs to drive the loop.
    """
    from analyst_agent.tui import run_tui as _run_tui

    _run_tui(agent, bus)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    load_dotenv()

    try:
        config = load_config(os.environ)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    bus = Bus()
    link = SmacLink(config)
    try:
        link.join_or_load()
    except JoinFailed as exc:
        print(str(exc), file=sys.stderr)
        return 2

    brain = Brain(config.anthropic_api_key, config.model, bus)
    guard = Guard(config.max_replies_per_min, config.max_hops)
    agent = Agent(link, brain, guard, bus, config)

    if args.chat_only:
        asyncio.run(_run_chat_repl(agent))
        return 0

    # `--once` has no meaningful TUI presentation (an interactive app that
    # exits after one mention isn't interactive) -- route it through the
    # headless path even if `--headless` wasn't also passed.
    if args.headless or args.once:
        asyncio.run(_run_headless(agent, bus, once=args.once))
        return 0

    run_tui(agent, bus)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
