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
        dead_sockets = []
        for websocket in list(self._channels.get(channel_id, set())):
            try:
                await websocket.send_json(payload)
            except Exception:
                dead_sockets.append(websocket)
        for websocket in dead_sockets:
            self.disconnect(channel_id, websocket)


manager = ConnectionManager()
