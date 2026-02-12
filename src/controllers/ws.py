from fastapi import WebSocket


class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self):
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, client_id: str, websocket: WebSocket):
        await websocket.accept()
        self._connections[client_id] = websocket

    def disconnect(self, client_id: str):
        self._connections.pop(client_id, None)

    def disconnect_all(self):
        self._connections.clear()

    async def send_json(self, client_id: str, data: dict):
        ws = self._connections.get(client_id)
        if ws:
            await ws.send_json(data)

    async def broadcast(self, data: dict):
        for ws in self._connections.values():
            await ws.send_json(data)

    @property
    def active_connections(self) -> list[str]:
        return list(self._connections.keys())
