import asyncio
import httpx
import pytest

from tests.helpers import generate_test_file


@pytest.mark.anyio
async def test_websocket_upload_http_download(test_client: httpx.AsyncClient, websocket_client):
    """Tests a browser-like upload (WebSocket) and a cURL-like download (HTTP)."""
    uid = "ws-http-journey"
    file_content, file_metadata = generate_test_file(size_in_kb=64)

    async def sender():
        with websocket_client.websocket_connect(f"/send/{uid}") as ws:
            await asyncio.sleep(0.1)

            ws.send_json({
                'file_name': file_metadata.name,
                'file_size': file_metadata.size,
                'file_type': file_metadata.type
            })
            await asyncio.sleep(1.0)

            # Wait for receiver to connect
            response = ws.receive_text()
            await asyncio.sleep(0.1)
            assert response == "Go for file chunks"

            # Send file
            chunk_size = 4096
            for i in range(0, len(file_content), chunk_size):
                ws.send_bytes(file_content[i:i + chunk_size])
                await asyncio.sleep(0.025)

            ws.send_bytes(b'')  # End of file
            await asyncio.sleep(0.1)

    async def receiver():
        await asyncio.sleep(1.0)
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'}

        async with test_client.stream("GET", f"/{uid}?download=true", headers=headers) as response:
            await asyncio.sleep(0.1)

            response.raise_for_status()
            assert response.headers['content-length'] == str(file_metadata.size)
            assert f"filename={file_metadata.name}" in response.headers['content-disposition']
            await asyncio.sleep(0.1)

            downloaded_content = b''
            async for chunk in response.aiter_bytes(4096):
                if not chunk or len(downloaded_content) >= file_metadata.size:
                    break
                downloaded_content += chunk
                await asyncio.sleep(0.025)

            assert len(downloaded_content) == file_metadata.size
            assert downloaded_content == file_content
            await asyncio.sleep(0.1)

    t1 = asyncio.create_task(asyncio.wait_for(sender(), timeout=15))
    t2 = asyncio.create_task(asyncio.wait_for(receiver(), timeout=15))
    await asyncio.gather(t1, t2, return_exceptions=True)


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
            await asyncio.sleep(1.0)

            response.raise_for_status()
            assert response.status_code == 200
        await asyncio.sleep(0.1)

    async def receiver():
        await asyncio.sleep(1.0)
        response = await test_client.get(f"/{uid}?download=true")
        await asyncio.sleep(0.1)

        response.raise_for_status()
        assert response.content == file_content
        assert len(response.content) == file_metadata.size
        await asyncio.sleep(0.1)

    t1 = asyncio.create_task(asyncio.wait_for(sender(), timeout=15))
    t2 = asyncio.create_task(asyncio.wait_for(receiver(), timeout=15))
    await asyncio.gather(t1, t2, return_exceptions=True)
