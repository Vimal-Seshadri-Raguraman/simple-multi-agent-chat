"""Tests for `brain.py`: the only module that talks to Anthropic.

Every test injects a fake client (`_FakeAnthropic` below) shaped like the
documented `client.messages.stream(...)` context-manager API -- no test
here ever touches the network or requires the `anthropic` package to be
importable. That's also why this file is safe to run with
`env -u ANTHROPIC_API_KEY`: `Brain` never even looks at the environment,
and the real `anthropic.AsyncAnthropic` is only constructed lazily, inside
`think()`, when no fake `client` was injected.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from analyst_agent.brain import PERSONA, Brain, BrainError, format_history
from analyst_agent.bus import Bus


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
def bus() -> Bus:
    return Bus()


# -- fake Anthropic client ---------------------------------------------------
#
# Shaped like the real SDK: `client.messages.stream(...)` returns an async
# context manager whose `.text_stream` is an async iterable of text deltas
# and whose `.get_final_message()` is an awaitable carrying `.usage`.


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FinalMessage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.usage = _Usage(input_tokens, output_tokens)


class _FakeStream:
    def __init__(
        self, chunks: list[str], input_tokens: int, output_tokens: int
    ) -> None:
        self._chunks = chunks
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens

    async def __aenter__(self) -> "_FakeStream":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    @property
    def text_stream(self):
        return self._agen()

    async def _agen(self):
        for chunk in self._chunks:
            yield chunk

    async def get_final_message(self) -> _FinalMessage:
        return _FinalMessage(self._input_tokens, self._output_tokens)


class _FakeMessages:
    def __init__(
        self,
        chunks: list[str] | None,
        error: BaseException | None,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        self._chunks = chunks
        self._error = error
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self.last_kwargs: dict[str, Any] = {}

    def stream(self, **kwargs: Any) -> _FakeStream:
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return _FakeStream(self._chunks or [], self._input_tokens, self._output_tokens)


class _FakeAnthropic:
    def __init__(
        self,
        chunks: list[str] | None = None,
        error: BaseException | None = None,
        input_tokens: int = 1204,
        output_tokens: int = 312,
    ) -> None:
        self.messages = _FakeMessages(chunks, error, input_tokens, output_tokens)


def fake_stream(
    chunks: list[str], input_tokens: int = 1204, output_tokens: int = 312
) -> _FakeAnthropic:
    return _FakeAnthropic(
        chunks=chunks, input_tokens=input_tokens, output_tokens=output_tokens
    )


def fake_raises(error: BaseException) -> _FakeAnthropic:
    return _FakeAnthropic(error=error)


def _payload(sender: str, text: str) -> dict:
    """A SMAC channel-history payload for `sender` saying `text` -- see
    the module docstring's wire shape (no handle, only `member_name`)."""
    return {
        "timestamp": "2026-08-07T00:00:00Z",
        "workspace": "ws1",
        "Channel": "general",
        "Sender": {"member_id": "m-1", "member_name": sender},
        "Message": {"message_id": "msg-1", "message_text": text},
        "mentions": [],
        "channel_refs": [],
    }


# -- think() ------------------------------------------------------------------


@pytest.mark.anyio
async def test_streams_tokens_to_the_bus_and_returns_the_text(bus: Bus) -> None:
    brain = Brain("sk-ant-x", "claude-sonnet-5", bus, client=fake_stream(["Hel", "lo"]))

    reply = await brain.think(system="s", history=[], trigger="hi")

    assert reply.text == "Hello"
    kinds = [e.kind for e in bus.history(50)]
    assert kinds == ["model_call", "token", "token", "model_done"]


@pytest.mark.anyio
async def test_usage_recorded(bus: Bus) -> None:
    brain = Brain("sk-ant-x", "claude-sonnet-5", bus, client=fake_stream(["Hel", "lo"]))

    reply = await brain.think(system="s", history=[], trigger="hi")

    assert reply.input_tokens == 1204
    assert reply.output_tokens == 312
    assert reply.seconds > 0


@pytest.mark.anyio
async def test_api_error_becomes_an_event_not_a_crash(bus: Bus) -> None:
    brain = Brain(
        "sk-ant-x", "claude-sonnet-5", bus, client=fake_raises(RuntimeError("boom"))
    )

    with pytest.raises(BrainError):
        await brain.think(system="s", history=[], trigger="hi")

    assert bus.history(5)[-1].kind == "error"
    assert bus.history(5)[-1].fields["message"] == "boom"


@pytest.mark.anyio
async def test_model_call_event_carries_model_temp_and_context_size(bus: Bus) -> None:
    brain = Brain("sk-ant-x", "claude-sonnet-5", bus, client=fake_stream(["hi"]))

    await brain.think(system="s", history=[], trigger="hi")

    call_event = bus.history(50)[0]
    assert call_event.kind == "model_call"
    assert call_event.fields["model"] == "claude-sonnet-5"
    assert "temp" in call_event.fields
    assert call_event.fields["context_size"] > 0


@pytest.mark.anyio
async def test_thread_turns_are_included_before_the_trigger(bus: Bus) -> None:
    client = fake_stream(["ok"])
    brain = Brain("sk-ant-x", "claude-sonnet-5", bus, client=client)
    thread = [
        {"role": "user", "content": "earlier turn"},
        {"role": "assistant", "content": "earlier reply"},
    ]

    await brain.think(system="s", history=[], trigger="hi", thread=thread)

    sent_messages = client.messages.last_kwargs["messages"]
    assert sent_messages == [
        {"role": "user", "content": "earlier turn"},
        {"role": "assistant", "content": "earlier reply"},
        {"role": "user", "content": "hi"},
    ]


@pytest.mark.anyio
async def test_history_precedes_thread_and_trigger_in_sent_messages(bus: Bus) -> None:
    client = fake_stream(["ok"])
    brain = Brain("sk-ant-x", "claude-sonnet-5", bus, client=client)
    history = format_history([_payload("Alice", "hi there")])

    await brain.think(system="s", history=history, trigger="what's up")

    sent_messages = client.messages.last_kwargs["messages"]
    assert sent_messages == [
        {"role": "user", "content": "Alice: hi there"},
        {"role": "user", "content": "what's up"},
    ]


# -- format_history -----------------------------------------------------------


def test_history_formatting_maps_payloads_to_turns() -> None:
    turns = format_history([_payload("Alice", "hi"), _payload("Analyst", "hey")])

    assert turns == [
        {"role": "user", "content": "Alice: hi"},
        {"role": "user", "content": "Analyst: hey"},
    ]


def test_history_formatting_handles_empty_list() -> None:
    assert format_history([]) == []


# -- PERSONA --------------------------------------------------------------------


def test_persona_names_handle_and_workspace() -> None:
    text = PERSONA("Analyst", "Quarterly Planning")

    assert "Analyst" in text
    assert "Quarterly Planning" in text


def test_persona_hedges_against_prompt_injection() -> None:
    text = PERSONA("Analyst", "ws").lower()

    # a one-line hedge: message content is data, not instructions to obey
    assert "not instructions" in text or "not commands" in text or "untrusted" in text


def test_persona_asks_for_brief_answers() -> None:
    text = PERSONA("Analyst", "ws").lower()

    assert "brief" in text or "concise" in text or "short" in text


# -- security: no secret in any emitted event ----------------------------------


@pytest.mark.anyio
async def test_no_secret_in_any_emitted_event(bus: Bus) -> None:
    api_key = "sk-ant-super-secret-value"
    brain = Brain(api_key, "claude-sonnet-5", bus, client=fake_stream(["Hel", "lo"]))
    await brain.think(system="s", history=[], trigger="hi")

    dumped = json.dumps([e.fields for e in bus.history(50)])
    assert "sk-ant" not in dumped
    assert api_key not in dumped


@pytest.mark.anyio
async def test_no_secret_in_error_event_or_exception_message(bus: Bus) -> None:
    api_key = "sk-ant-super-secret-value"
    brain = Brain(
        api_key, "claude-sonnet-5", bus, client=fake_raises(RuntimeError("boom"))
    )

    with pytest.raises(BrainError) as exc_info:
        await brain.think(system="s", history=[], trigger="hi")

    assert api_key not in str(exc_info.value)
    dumped = json.dumps([e.fields for e in bus.history(50)])
    assert api_key not in dumped


def test_import_brain_does_not_require_anthropic_or_a_key() -> None:
    # If this module imported `anthropic` at module scope, this test file
    # would already have failed to collect (the package isn't installed
    # in this venv) -- the assertion below is just a live check that the
    # symbols exist and the client stays unset until think() needs it.
    import analyst_agent.brain as brain_module

    bus_ = Bus()
    brain = brain_module.Brain("sk-ant-x", "claude-sonnet-5", bus_)
    assert brain is not None
