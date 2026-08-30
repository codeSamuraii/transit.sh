import anyio
import pytest

from tests.helpers import generate_test_file
from tests.ws_client import WebSocketTestClient
from tests.http_client import HTTPTestClient


@pytest.mark.anyio
async def test_websocket_upload_http_download(test_client: HTTPTestClient, websocket_client: WebSocketTestClient):
    """Tests a browser-like upload (WebSocket) and a cURL-like download (HTTP)."""
    uid = "ws-http-journey"
    file_content, file_metadata = generate_test_file(size_in_kb=64)

    async def sender():
        async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
            await anyio.sleep(0.1)
            await ws.upload_with_metadata(file_content, file_metadata, delay=0.025)
            await anyio.sleep(0.1)

    async def receiver():
        await anyio.sleep(1.0)
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'}

        async with test_client.stream("GET", f"/{uid}?download=true", headers=headers) as response:
            await anyio.sleep(0.1)

            response.raise_for_status()
            assert response.headers['content-length'] == str(file_metadata.size), f"Content-Length header should be {file_metadata.size}, got {response.headers.get('content-length')}"
            assert f"filename={file_metadata.name}" in response.headers['content-disposition'], f"Content-Disposition should contain filename={file_metadata.name}"
            await anyio.sleep(0.1)

            downloaded_content = b''
            async for chunk in response.aiter_bytes(4096):
                if not chunk or len(downloaded_content) >= file_metadata.size:
                    break
                downloaded_content += chunk
                await anyio.sleep(0.025)

            assert len(downloaded_content) == file_metadata.size, f"Downloaded size should be {file_metadata.size}, got {len(downloaded_content)}"
            assert downloaded_content == file_content, f"Downloaded content should match uploaded content"
            await anyio.sleep(0.1)

    async with anyio.create_task_group() as tg:
        tg.start_soon(sender)
        tg.start_soon(receiver)


@pytest.mark.anyio
async def test_http_upload_http_download(test_client: HTTPTestClient):
    """Tests a cURL-like upload (HTTP PUT) and download (HTTP GET)."""
    uid = "http-http-journey"
    file_content, file_metadata = generate_test_file(size_in_kb=64)

    async def sender():
        response = await test_client.upload_file_http(uid, file_content, file_metadata)
        await anyio.sleep(1.0)

        response.raise_for_status()
        assert response.status_code == 200, f"HTTP upload should return 200, got {response.status_code}"
        await anyio.sleep(0.1)

    async def receiver():
        await anyio.sleep(1.0)
        response = await test_client.get(f"/{uid}?download=true")
        await anyio.sleep(0.1)

        response.raise_for_status()
        assert response.content == file_content, "Downloaded content should match uploaded content"
        assert len(response.content) == file_metadata.size, f"Downloaded size should be {file_metadata.size}, got {len(response.content)}"
        await anyio.sleep(0.1)

    async with anyio.create_task_group() as tg:
        tg.start_soon(sender)
        tg.start_soon(receiver)
