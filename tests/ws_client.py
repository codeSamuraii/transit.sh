import json
import anyio
import websockets
from contextlib import asynccontextmanager
from typing import Any

from lib.metadata import FileMetadata


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

    # Helper methods for common WebSocket test operations

    async def send_file_metadata(self, file_metadata: FileMetadata):
        """Send file metadata via WebSocket."""
        await self.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

    async def send_custom_metadata(self, filename: str, filesize: int, filetype: str):
        """Send custom file metadata via WebSocket (for testing mismatches)."""
        await self.send_json({
            'file_name': filename,
            'file_size': filesize,
            'file_type': filetype
        })

    async def wait_for_go_signal(self, timeout: float = 5.0, fail: bool = True):
        """Wait for and verify the 'Go for file chunks' signal."""
        try:
            with anyio.fail_after(timeout):
                response = await self.recv()
                assert response == "Go for file chunks", f"Expected 'Go for file chunks', got '{response}'"
                return response
        except TimeoutError as e:
            if fail: raise
            else: print(f"*** Silenced TimeoutError: {e}")

    async def upload_file_chunks(self, file_content: bytes, chunk_size: int = 4096, delay: float = 0.01):
        """Upload file content in chunks via WebSocket."""
        for i in range(0, len(file_content), chunk_size):
            chunk = file_content[i:i + chunk_size]
            await self.send_bytes(chunk)
            if delay > 0:
                await anyio.sleep(delay)

        # Send empty chunk to signal end
        await self.send_bytes(b'')

    async def upload_partial_chunks(self, file_content: bytes, max_bytes: int,
                                    chunk_size: int = 4096, delay: float = 0.01) -> int:
        """Upload partial file content and return bytes sent (without end marker)."""
        bytes_sent = 0
        for i in range(0, min(len(file_content), max_bytes), chunk_size):
            chunk_end = min(i + chunk_size, max_bytes, len(file_content))
            chunk = file_content[i:chunk_end]
            await self.send_bytes(chunk)
            bytes_sent += len(chunk)
            if delay > 0:
                await anyio.sleep(delay)
            if bytes_sent >= max_bytes:
                break
        return bytes_sent

    async def upload_with_metadata(self, file_content: bytes, file_metadata: FileMetadata, chunk_size: int = 4096, delay: float = 0.01, wait_for_go: float = 5.0):
        """Complete upload flow: send metadata, wait for signal, upload chunks."""
        await self.send_file_metadata(file_metadata)
        await self.wait_for_go_signal(timeout=wait_for_go)
        await self.upload_file_chunks(file_content, chunk_size, delay)

    def parse_resume_position(self, message: str) -> int:
        """Parse resume position from WebSocket message."""
        assert "Resume from:" in message, f"Expected 'Resume from:' message, got '{message}'"
        return int(message.split(":")[1].strip())


class WebSocketTestClient:
    def __init__(self, base_url: str):
        self.base_url = base_url

    @asynccontextmanager
    async def websocket_connect(self, path: str):
        """Connect to a WebSocket endpoint."""
        url = f"{self.base_url}{path}"
        async with websockets.connect(url) as websocket:
            yield WebSocketWrapper(websocket)
