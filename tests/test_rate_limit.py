from app import rate_limit
from app.rate_limit import SlidingWindowRateLimiter
from tests.conftest import founder_auth, founder_headers


def test_allows_up_to_max_events_then_blocks(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: now[0])
    limiter = SlidingWindowRateLimiter(max_events=10, window_seconds=10)

    for _ in range(10):
        assert limiter.allow("member-1") is True

    assert limiter.allow("member-1") is False


def test_window_slides_stale_events_are_evicted(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: now[0])
    limiter = SlidingWindowRateLimiter(max_events=10, window_seconds=10)

    for _ in range(10):
        assert limiter.allow("member-1") is True
    assert limiter.allow("member-1") is False

    now[0] = 10.1  # advance past the window; the old events should be pruned
    assert limiter.allow("member-1") is True


def test_keys_are_independent(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: now[0])
    limiter = SlidingWindowRateLimiter(max_events=10, window_seconds=10)

    for _ in range(10):
        assert limiter.allow("member-1") is True
    assert limiter.allow("member-1") is False

    # A different key has its own independent budget.
    assert limiter.allow("member-2") is True


def _setup_channel_with_agents(client):
    founder = founder_auth(client, "w1")
    channel = client.post(
        f"/workspaces/{founder['workspace_id']}/channels",
        json={"channel_name": "team-chat"},
        headers=founder_headers(client, "w1"),
    ).json()
    agent = client.post(
        "/members/agents",
        json={"member_name": "Bot"},
        headers=founder_headers(client, "w1"),
    ).json()
    other_agent = client.post(
        "/members/agents",
        json={"member_name": "OtherBot"},
        headers=founder_headers(client, "w1"),
    ).json()
    for a in (agent, other_agent):
        client.post(
            f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/members",
            json={"member_id": a["member_id"]},
            headers=founder_headers(client, "w1"),
        )
    return founder, channel, agent, other_agent


def _post(client, workspace, channel, agent, text="hello"):
    return client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        json={"message_text": text},
        headers={"X-API-Key": agent["api_key"]},
    )


def test_rate_limited_member_gets_429_and_does_not_post(client, monkeypatch):
    from app import rate_limit as rate_limit_module

    small_limiter = rate_limit_module.SlidingWindowRateLimiter(
        max_events=3, window_seconds=60
    )
    monkeypatch.setattr(rate_limit_module, "post_limiter", small_limiter)

    workspace, channel, agent, other_agent = _setup_channel_with_agents(client)

    for _ in range(3):
        response = _post(client, workspace, channel, agent)
        assert response.status_code == 200

    response = _post(client, workspace, channel, agent, text="fourth message")
    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "rate_limited",
            "message": "Posting too fast — wait a moment",
        }
    }

    # The rejected post never made it into the channel history.
    history = client.get(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
        headers={"X-API-Key": agent["api_key"]},
    ).json()
    assert len(history) == 3
    assert all(m["Message"]["message_text"] != "fourth message" for m in history)


def test_rate_limit_is_per_member(client, monkeypatch):
    from app import rate_limit as rate_limit_module

    small_limiter = rate_limit_module.SlidingWindowRateLimiter(
        max_events=3, window_seconds=60
    )
    monkeypatch.setattr(rate_limit_module, "post_limiter", small_limiter)

    workspace, channel, agent, other_agent = _setup_channel_with_agents(client)

    for _ in range(3):
        assert _post(client, workspace, channel, agent).status_code == 200
    assert _post(client, workspace, channel, agent).status_code == 429

    # A different member in the same workspace/channel is unaffected.
    response = _post(client, workspace, channel, other_agent)
    assert response.status_code == 200
