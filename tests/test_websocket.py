import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest import founder_auth, founder_headers


def _setup_channel_with_agent(client):
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
    client.post(
        f"/workspaces/{founder['workspace_id']}/channels/{channel['channel_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=founder_headers(client, "w1"),
    )
    return founder, channel, agent


def test_websocket_receives_broadcast_message(client):
    workspace, channel, agent = _setup_channel_with_agent(client)
    ws_url = (
        f"/ws/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}"
    )

    # The founder isn't a channel member yet, so the connection is rejected
    # before being accepted.
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"{ws_url}?token={workspace['access_token']}"):
            pass
    assert exc_info.value.code == 4403

    # The founder is already a workspace member (founding); add them to this
    # channel — a non-default channel, so they aren't auto-joined to it.
    client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/members",
        json={"member_id": workspace["member_id"]},
        headers=founder_headers(client, "w1"),
    )

    # Two real, simultaneously-connected clients (the founder and the agent) — a broadcast
    # from one member's message post must reach both connected sockets, not just one.
    with client.websocket_connect(
        f"{ws_url}?token={workspace['access_token']}"
    ) as ws_human, client.websocket_connect(
        ws_url, headers={"X-API-Key": agent["api_key"]}
    ) as ws_agent:
        client.post(
            f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/messages",
            json={"message_text": "hello from agent"},
            headers={"X-API-Key": agent["api_key"]},
        )
        for ws in (ws_human, ws_agent):
            received = ws.receive_json()
            assert received["Message"]["message_text"] == "hello from agent"
            assert received["Sender"]["member_id"] == agent["member_id"]


def test_websocket_rejects_non_channel_member(client):
    workspace, channel, _ = _setup_channel_with_agent(client)
    outsider = client.post(
        "/members/agents",
        json={"member_name": "Outsider"},
        headers=founder_headers(client, "w1"),
    ).json()
    ws_url = (
        f"/ws/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}"
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            ws_url, headers={"X-API-Key": outsider["api_key"]}
        ):
            pass
    assert exc_info.value.code == 4403


def test_websocket_rejects_missing_credentials(client):
    workspace, channel, _ = _setup_channel_with_agent(client)
    ws_url = (
        f"/ws/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}"
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(ws_url):
            pass
    assert exc_info.value.code == 4401


def test_websocket_rejects_garbage_token(client):
    workspace, channel, _ = _setup_channel_with_agent(client)
    ws_url = (
        f"/ws/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}"
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"{ws_url}?token=garbage.not.a.jwt"):
            pass
    assert exc_info.value.code == 4401
