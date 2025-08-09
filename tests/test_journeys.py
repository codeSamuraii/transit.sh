import anyio
import httpx
import json
import pytest

from tests.helpers import generate_test_file
from tests.ws_client import WebSocketTestClient


@pytest.mark.anyio
async def test_websocket_upload_http_download(test_client: httpx.AsyncClient, websocket_client: WebSocketTestClient):
    """Tests a browser-like upload (WebSocket) and a cURL-like download (HTTP)."""
    uid = "ws-http-journey"
    file_content, file_metadata = generate_test_file(size_in_kb=64)

    async def sender():
        async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
            await anyio.sleep(0.1)

            await ws.websocket.send(json.dumps({
                'file_name': file_metadata.name,
                'file_size': file_metadata.size,
                'file_type': file_metadata.type
            }))
            await anyio.sleep(1.0)

            # Wait for receiver to connect
            response = await ws.websocket.recv()
            await anyio.sleep(0.1)
            assert response == "Go for file chunks"

            # Send file
            chunk_size = 4096
            for i in range(0, len(file_content), chunk_size):
                await ws.websocket.send(file_content[i:i + chunk_size])
                await anyio.sleep(0.025)

            await ws.websocket.send(b'')  # End of file
            await anyio.sleep(0.1)

    async def receiver():
        await anyio.sleep(1.0)
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'}

        async with test_client.stream("GET", f"/{uid}?download=true", headers=headers) as response:
            await anyio.sleep(0.1)

            response.raise_for_status()
            assert response.headers['content-length'] == str(file_metadata.size)
            assert f"filename={file_metadata.name}" in response.headers['content-disposition']
            await anyio.sleep(0.1)

            downloaded_content = b''
            async for chunk in response.aiter_bytes(4096):
                if not chunk or len(downloaded_content) >= file_metadata.size:
                    break
                downloaded_content += chunk
                await anyio.sleep(0.025)

            assert len(downloaded_content) == file_metadata.size
            assert downloaded_content == file_content
            await anyio.sleep(0.1)

    async with anyio.create_task_group() as tg:
        tg.start_soon(sender)
        tg.start_soon(receiver)


@pytest.mark.anyio
async def test_http_upload_http_download(test_client: httpx.AsyncClient):
    """Tests a cURL-like upload (HTTP PUT) and download (HTTP GET)."""
    uid = "http-http-journey"
    file_content, file_metadata = generate_test_file(size_in_kb=64)

    async def sender():
        headers = {
            'Content-Type': file_metadata.type,
            'Content-Length': str(file_metadata.size)
        }
        async with test_client.stream("PUT", f"/{uid}/{file_metadata.name}", content=file_content, headers=headers) as response:
            await anyio.sleep(1.0)

            response.raise_for_status()
            assert response.status_code == 200
        await anyio.sleep(0.1)

    async def receiver():
        await anyio.sleep(1.0)
        response = await test_client.get(f"/{uid}?download=true")
        await anyio.sleep(0.1)

        response.raise_for_status()
        assert response.content == file_content
        assert len(response.content) == file_metadata.size
        await anyio.sleep(0.1)

    async with anyio.create_task_group() as tg:
        tg.start_soon(sender)
        tg.start_soon(receiver)
