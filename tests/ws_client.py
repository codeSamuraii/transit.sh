import json
from contextlib import asynccontextmanager
from typing import Any

import websockets


class WebSocketWrapper:
    """Wrapper to provide a similar API to starlette.testclient.WebSocketTestSession."""
    def __init__(self, websocket):
        self.websocket = websocket

    async def send_text(self, data: str):
        await self.websocket.send(data)

    async def send_bytes(self, data: bytes):
        await self.websocket.send(data)

    async def send_json(self, data: Any, mode: str = "text"):
        text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        if mode == "text":
            await self.websocket.send(text)
        else:
            await self.websocket.send(text.encode("utf-8"))

    async def close(self, code: int = 1000, reason: str | None = None):
        await self.websocket.close(code, reason or "")

    async def receive_text(self) -> str:
        message = await self.websocket.recv()
        if isinstance(message, bytes):
            return message.decode("utf-8")
        return message

    async def receive_bytes(self) -> bytes:
        message = await self.websocket.recv()
        if isinstance(message, str):
            return message.encode("utf-8")
        return message

    async def receive_json(self, mode: str = "text") -> Any:
        message = await self.websocket.recv()
        if mode == "text":
            if isinstance(message, bytes):
                text = message.decode("utf-8")
            else:
                text = message
        else: # binary
            if isinstance(message, str):
                text = message
            else:
                text = message.decode("utf-8")
        return json.loads(text)

    async def recv(self):
        return await self.websocket.recv()


class WebSocketTestClient:
    def __init__(self, base_url: str):
        self.base_url = base_url

    @asynccontextmanager
    async def websocket_connect(self, path: str):
        """Connect to a WebSocket endpoint."""
        url = f"{self.base_url}{path}"
        async with websockets.connect(url) as websocket:
            yield WebSocketWrapper(websocket)
