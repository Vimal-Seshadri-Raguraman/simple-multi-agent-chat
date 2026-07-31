from fastapi import WebSocket


class ConnectionManager:
    """In-process, in-memory WebSocket registry: channel_id -> connected sockets."""

    def __init__(self) -> None:
        self._channels: dict[str, set[WebSocket]] = {}

    async def connect(self, channel_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._channels.setdefault(channel_id, set()).add(websocket)

    def disconnect(self, channel_id: str, websocket: WebSocket) -> None:
        sockets = self._channels.get(channel_id)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            del self._channels[channel_id]

    async def broadcast(self, channel_id: str, payload: dict) -> None:
        for websocket in list(self._channels.get(channel_id, set())):
            await websocket.send_json(payload)


manager = ConnectionManager()
