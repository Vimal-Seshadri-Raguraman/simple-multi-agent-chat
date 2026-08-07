"""Tests for `analyst_agent.smac_link`, driven entirely against
`httpx.MockTransport` -- no real network, no ANTHROPIC_API_KEY needed.
"""

from __future__ import annotations

import json
from collections.abc import Callable

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
    assert default_limit_request.url.params["limit"] == "20"

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
