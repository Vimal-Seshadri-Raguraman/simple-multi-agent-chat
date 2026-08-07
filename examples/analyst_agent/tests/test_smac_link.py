"""Tests for `analyst_agent.smac_link`, driven entirely against
`httpx.MockTransport` -- no real network, no ANTHROPIC_API_KEY needed.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

import analyst_agent.smac_link as paths
from analyst_agent.smac_link import Credentials, JoinFailed, SmacLink, credentials_path

JOIN_RESPONSE = {
    "account_id": "acc-1",
    "member_id": "mem-1",
    "handle": "analyst",
    "api_key": "smac-key-xyz",
    "workspace": {"workspace_id": "ws-1", "workspace_name": "Test Workspace"},
}

SAVED_CREDENTIALS = Credentials(
    member_id="mem-1",
    handle="analyst",
    api_key="smac-key-xyz",
    workspace_id="ws-1",
    workspace_name="Test Workspace",
)


def mock_join_transport() -> httpx.MockTransport:
    """A transport that only ever answers `POST /agents/join`, asserting
    the request body matches the invite code/name `cfg` carries."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/agents/join"
        assert json.loads(request.content) == {"code": "abc123", "name": "Analyst"}
        return httpx.Response(201, json=JOIN_RESPONSE)

    return httpx.MockTransport(handler)


def mock_never_join_transport() -> httpx.MockTransport:
    """A transport that fails the test the instant `/agents/join` is hit --
    used to prove a second run with saved credentials never redeems the
    (single-use) invite code again."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert (
            request.url.path != "/agents/join"
        ), "join must not be called on a second run"
        return httpx.Response(200, json=[])  # pragma: no cover - never reached

    return httpx.MockTransport(handler)


def mock_invalid_code() -> httpx.MockTransport:
    """The server's real 404 envelope for an unknown/expired/already-
    redeemed agent invite code (verbatim, `app/routers/invites.py`)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "code": "invalid_invite",
                    "message": "Invite is invalid or expired",
                }
            },
        )

    return httpx.MockTransport(handler)


def capture_request(cfg, action: Callable[[SmacLink], None]) -> httpx.Request:
    """Run `action` against a `SmacLink` that already has credentials (no
    join needed), and return the single request it sent."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    link = SmacLink(cfg, transport=httpx.MockTransport(handler))
    link.credentials = SAVED_CREDENTIALS
    action(link)
    assert len(captured) == 1
    return captured[0]


def test_join_persists_credentials_with_600(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CONFIG_HOME", tmp_path)
    link = SmacLink(cfg, transport=mock_join_transport())
    creds = link.join_or_load()
    assert creds.handle == "analyst" and creds.api_key == "smac-key-xyz"
    saved = credentials_path(cfg.agent_name)
    assert oct(saved.stat().st_mode)[-3:] == "600"


def test_second_run_loads_and_never_calls_join(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CONFIG_HOME", tmp_path)
    # First run: joins for real and persists credentials to tmp_path.
    SmacLink(cfg, transport=mock_join_transport()).join_or_load()

    # Second run: a fresh SmacLink over a transport that asserts /agents/join
    # is never requested -- it must load the saved credentials instead.
    second = SmacLink(cfg, transport=mock_never_join_transport())
    creds = second.join_or_load()
    assert creds.handle == "analyst" and creds.api_key == "smac-key-xyz"


def test_invalid_code_raises_actionable_error(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CONFIG_HOME", tmp_path)
    with pytest.raises(JoinFailed) as e:
        SmacLink(cfg, transport=mock_invalid_code()).join_or_load()
    assert "Invite is invalid or expired" in str(e.value)  # server envelope, verbatim
    assert "Settings → Invites" in str(e.value)  # how to get a new one


def test_requests_carry_the_api_key_header_and_never_the_url(cfg):
    request = capture_request(cfg, lambda link: link.post("ch1", "hi"))
    assert request.headers["X-API-Key"] == "smac-key-xyz"
    assert "smac-key-xyz" not in str(request.url)


def test_history_and_post_hit_the_documented_paths(cfg):
    history_request = capture_request(cfg, lambda link: link.history("ch1", limit=5))
    assert history_request.method == "GET"
    assert history_request.url.path == "/workspaces/ws-1/channels/ch1/messages"
    assert history_request.url.params["limit"] == "5"

    default_limit_request = capture_request(cfg, lambda link: link.history("ch1"))
    # F2 fix: `limit` (default 20) is the size of `history()`'s own
    # rolling TAIL buffer, not the page size sent over the wire -- the
    # server's real MAX_LIMIT is 15 (`app/routers/messages.py`), so the
    # HTTP request always asks for at most 15 regardless of what the
    # caller wants filled. `test_history_returns_the_tail_...` below is
    # the actual multi-page proof.
    assert default_limit_request.url.params["limit"] == "15"

    post_request = capture_request(cfg, lambda link: link.post("ch1", "hello"))
    assert post_request.method == "POST"
    assert post_request.url.path == "/workspaces/ws-1/channels/ch1/messages"
    assert json.loads(post_request.content) == {"message_text": "hello"}


def test_channels_and_mentions_hit_the_documented_paths(cfg):
    channels_request = capture_request(cfg, lambda link: link.channels())
    assert channels_request.url.path == "/workspaces/ws-1/channels"

    mentions_request = capture_request(cfg, lambda link: link.pending_mentions())
    assert mentions_request.url.path == "/mentions"

    ack_request = capture_request(cfg, lambda link: link.ack("mention-1"))
    assert ack_request.method == "POST"
    assert ack_request.url.path == "/mentions/mention-1/ack"


# -- F2: history() reaches the tail, not the head ---------------------------
#
# `GET .../messages` orders oldest-first with no cursor and clamps `limit`
# to 15 server-side (`app/routers/messages.py`) -- verified against a
# mocked transport that actually implements that pagination contract
# (unlike `capture_request`'s always-`[]` handler above, which can't
# exercise more than one page).


def _message_payload(message_id: str, text: str) -> dict[str, Any]:
    """A minimal `GET .../messages` row -- just enough shape for
    `history()`'s own logic (`Message.message_id` for the cursor) plus
    what `agent.py`'s `_guard_message`/`format_history` read."""
    return {
        "Channel": {"channel_id": "ch1", "channel_name": "general"},
        "Sender": {"member_id": "mem-1", "member_name": "analyst"},
        "Message": {"message_id": message_id, "message_text": text},
    }


def _paginated_messages_transport(
    all_messages: list[dict[str, Any]], requests: list[httpx.Request]
) -> httpx.MockTransport:
    """A transport that actually implements the real server's forward-
    cursor pagination contract over `all_messages` (oldest-first, `after`
    is strictly-greater on `message_id` position, `limit` honored as
    given -- the real server additionally clamps to 15, but `history()`
    itself never asks for more than that, so this fake doesn't need to
    enforce the clamp to still prove the client-side pager correct)."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        after = request.url.params.get("after")
        limit = int(request.url.params["limit"])
        if after is None:
            start = 0
        else:
            start = (
                next(
                    i
                    for i, m in enumerate(all_messages)
                    if m["Message"]["message_id"] == after
                )
                + 1
            )
        page = all_messages[start : start + limit]
        return httpx.Response(200, json=page)

    return httpx.MockTransport(handler)


def test_history_returns_the_tail_not_the_head_across_multiple_pages(cfg):
    """37 messages total -- more than one page (15) and more than the
    requested tail (20). `history(limit=20)` must come back with the
    NEWEST 20 (m18..m37), reached by paging the whole channel forward,
    not the channel's first 15 messages a naive single request would
    silently return (the F2 bug)."""
    all_messages = [_message_payload(f"m{i}", f"text {i}") for i in range(1, 38)]
    requests: list[httpx.Request] = []
    link = SmacLink(
        cfg, transport=_paginated_messages_transport(all_messages, requests)
    )
    link.credentials = SAVED_CREDENTIALS

    result = link.history("ch1", limit=20)

    assert [m["Message"]["message_id"] for m in result] == [
        f"m{i}" for i in range(18, 38)
    ]
    assert len(requests) == 3  # 15 + 15 + 7 to walk the whole channel once
    assert all(int(r.url.params["limit"]) <= 15 for r in requests)


def test_history_second_call_pages_only_whats_new_since_the_last_one(cfg):
    """Once a channel has been paged through once, a later call (the
    next mention) must be INCREMENTAL: only the messages posted since
    the last call are fetched, not a re-page of the whole channel."""
    messages = [_message_payload(f"m{i}", f"text {i}") for i in range(1, 17)]  # 16
    requests: list[httpx.Request] = []
    link = SmacLink(cfg, transport=_paginated_messages_transport(messages, requests))
    link.credentials = SAVED_CREDENTIALS

    first = link.history("ch1", limit=20)
    assert [m["Message"]["message_id"] for m in first] == [
        f"m{i}" for i in range(1, 17)
    ]
    assert len(requests) == 2  # 15 + 1 (short page -> the real end, for now)

    messages.append(_message_payload("m17", "text 17"))
    messages.append(_message_payload("m18", "text 18"))

    second = link.history("ch1", limit=20)
    assert [m["Message"]["message_id"] for m in second] == [
        f"m{i}" for i in range(1, 19)
    ]
    assert len(requests) == 3  # exactly one more request, not a re-page from m1


def test_history_rolling_buffer_never_exceeds_the_requested_limit(cfg):
    """The tail buffer stays bounded at `limit` even once the channel has
    grown well past it -- oldest entries fall off as newer ones arrive,
    same rolling-window behavior `test_history_returns_the_tail...`
    exercises across pages, checked here directly against the buffer
    size after several incremental calls."""
    messages = [_message_payload(f"m{i}", f"text {i}") for i in range(1, 6)]
    requests: list[httpx.Request] = []
    link = SmacLink(cfg, transport=_paginated_messages_transport(messages, requests))
    link.credentials = SAVED_CREDENTIALS

    for i in range(6, 26):
        messages.append(_message_payload(f"m{i}", f"text {i}"))
        result = link.history("ch1", limit=5)
        assert len(result) <= 5

    assert [m["Message"]["message_id"] for m in result] == [
        "m21",
        "m22",
        "m23",
        "m24",
        "m25",
    ]


def test_join_or_load_requires_a_code_when_nothing_is_saved(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CONFIG_HOME", tmp_path)
    from dataclasses import replace

    codeless_cfg = replace(cfg, agent_code=None)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not call the server without a code or saved creds")

    link = SmacLink(codeless_cfg, transport=httpx.MockTransport(handler))
    with pytest.raises(JoinFailed) as e:
        link.join_or_load()
    assert "SMAC_AGENT_CODE" in str(e.value)


def test_secrets_never_appear_in_a_raised_error(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CONFIG_HOME", tmp_path)
    with pytest.raises(JoinFailed) as e:
        SmacLink(cfg, transport=mock_invalid_code()).join_or_load()
    assert cfg.anthropic_api_key not in str(e.value)
