import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest import human_headers, human_member_id, human_token


def _setup_channel_with_agent(client):
    workspace = client.post(
        "/workspaces",
        json={"workspace_name": "Acme"},
        headers=human_headers(client, "m_1"),
    ).json()
    channel = client.post(
        f"/workspaces/{workspace['workspace_id']}/channels",
        json={"channel_name": "general"},
        headers=human_headers(client, "m_1"),
    ).json()
    agent = client.post(
        "/members/agents",
        json={"member_name": "Bot"},
        headers=human_headers(client, "m_1"),
    ).json()
    client.post(
        f"/workspaces/{workspace['workspace_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=human_headers(client, "m_1"),
    )
    client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/members",
        json={"member_id": agent["member_id"]},
        headers=human_headers(client, "m_1"),
    )
    return workspace, channel, agent


def test_websocket_receives_broadcast_message(client):
    workspace, channel, agent = _setup_channel_with_agent(client)
    ws_url = (
        f"/ws/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}"
    )

    # m_1 (the human who created the workspace/channel) isn't a channel member yet,
    # so the connection is rejected before being accepted.
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"{ws_url}?token={human_token(client, 'm_1')}"):
            pass
    assert exc_info.value.code == 4403

    # m_1 is already a workspace member (auto-added at creation); add them to
    # this channel — a non-default channel, so they aren't auto-joined to it.
    client.post(
        f"/workspaces/{workspace['workspace_id']}/channels/{channel['channel_id']}/members",
        json={"member_id": human_member_id(client, "m_1")},
        headers=human_headers(client, "m_1"),
    )

    # Two real, simultaneously-connected clients (the human and the agent) — a broadcast
    # from one member's message post must reach both connected sockets, not just one.
    with client.websocket_connect(
        f"{ws_url}?token={human_token(client, 'm_1')}"
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
        headers=human_headers(client, "m_1"),
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
