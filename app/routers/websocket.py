from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import app.database as database
from app.auth import resolve_member
from app.models import Channel, ChannelMember
from app.ws_manager import manager

router = APIRouter()


@router.websocket("/ws/workspaces/{workspace_id}/channels/{channel_id}")
async def channel_websocket(
    websocket: WebSocket, workspace_id: str, channel_id: str
) -> None:
    # Look up SessionLocal via the module (not a top-level `from ... import`) so that
    # tests can monkeypatch app.database.SessionLocal for the duration of a test —
    # a direct `from app.database import SessionLocal` would bind the name at import
    # time and never see the patched value.
    db = database.SessionLocal()
    try:
        member = resolve_member(
            db,
            websocket.headers.get("x-dev-member-id"),
            websocket.headers.get("x-dev-member-name"),
            websocket.headers.get("x-api-key"),
        )
        if member is None:
            await websocket.close(code=4401)
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
    finally:
        db.close()
