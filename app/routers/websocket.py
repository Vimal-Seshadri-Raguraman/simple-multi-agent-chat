from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import app.database as database
from app.auth import resolve_ws_credential
from app.models import Channel, ChannelMember
from app.ws_manager import event_manager, manager

router = APIRouter()


@router.websocket("/ws/workspaces/{workspace_id}/channels/{channel_id}")
async def channel_websocket(
    websocket: WebSocket, workspace_id: str, channel_id: str
) -> None:
    # Look up SessionLocal via the module (not a top-level `from ... import`) so that
    # tests can monkeypatch app.database.SessionLocal for the duration of a test —
    # a direct `from app.database import SessionLocal` would bind the name at import
    # time and never see the patched value.
    #
    # The DB session is scoped to just the auth/membership checks below and is
    # closed before entering the message-receive loop, so it isn't held checked
    # out of the connection pool for the entire lifetime of the WebSocket.
    with database.SessionLocal() as db:
        raw_credential = websocket.query_params.get("token") or websocket.headers.get(
            "x-api-key"
        )
        member = resolve_ws_credential(db, raw_credential)
        if member is None:
            await websocket.close(code=4401)
            return

        # The workspace wall: a token only works inside its own workspace.
        # Same close code as an unknown channel below -- uniform, so a
        # foreign workspace can't be distinguished from a nonexistent one.
        if member.workspace_id != workspace_id:
            await websocket.close(code=4404)
            return

        channel = (
            db.query(Channel)
            .filter(
                Channel.channel_id == channel_id, Channel.workspace_id == workspace_id
            )
            .first()
        )
        if channel is None:
            await websocket.close(code=4404)
            return

        is_member = (
            db.query(ChannelMember)
            .filter(
                ChannelMember.channel_id == channel_id,
                ChannelMember.member_id == member.member_id,
            )
            .first()
        )
        if is_member is None:
            await websocket.close(code=4403)
            return

    await manager.connect(channel_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(channel_id, websocket)


@router.websocket("/ws/workspaces/{workspace_id}/members/me/events")
async def member_events_websocket(websocket: WebSocket, workspace_id: str) -> None:
    """A member's private event feed: live mention pushes (Task 4).

    Unlike the channel socket, this is keyed by member_id, not channel_id --
    a member gets exactly one feed of everything addressed to them,
    independent of which channels they belong to. Auth/wall mirror the
    channel socket exactly (see its docstring for the session-scoping
    rationale).
    """
    with database.SessionLocal() as db:
        raw_credential = websocket.query_params.get("token") or websocket.headers.get(
            "x-api-key"
        )
        member = resolve_ws_credential(db, raw_credential)
        if member is None:
            await websocket.close(code=4401)
            return

        # Same workspace wall as the channel socket: a token only works
        # inside its own workspace, and a foreign workspace can't be
        # distinguished from a nonexistent one.
        if member.workspace_id != workspace_id:
            await websocket.close(code=4404)
            return

    await event_manager.connect(member.member_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        event_manager.disconnect(member.member_id, websocket)
