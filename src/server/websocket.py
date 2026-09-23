import json, logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

logger = logging.getLogger(__name__)
router = APIRouter()


class ConnectionManager:
    def __init__(self):
        self.active: dict[str, WebSocket] = {}
        self.global_listeners: list[WebSocket] = []

    async def connect(self, ws: WebSocket, session_id: str):
        await ws.accept()
        if session_id:
            self.active[session_id] = ws
        else:
            self.global_listeners.append(ws)

    def disconnect(self, ws: WebSocket, session_id: str):
        if session_id and session_id in self.active:
            if self.active[session_id] == ws:
                del self.active[session_id]
        elif ws in self.global_listeners:
            self.global_listeners.remove(ws)

    async def broadcast(self, session_id: str, event: dict):
        msg = json.dumps(event, ensure_ascii=False)
        # Send to session-specific listener
        ws = self.active.get(session_id)
        if ws:
            try:
                await ws.send_text(msg)
            except Exception:
                pass
        # Also send to global listeners
        for gws in self.global_listeners:
            try:
                await gws.send_text(msg)
            except Exception:
                pass


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket,
                             session_id: str = Query("")):
    await manager.connect(ws, session_id)
    try:
        while True:
            data = await ws.receive_text()
            if data == 'ping':
                await ws.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        manager.disconnect(ws, session_id)
