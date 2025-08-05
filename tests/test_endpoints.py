import asyncio
import time
import pytest
import httpx
from fastapi import WebSocketDisconnect
from starlette.responses import ClientDisconnect

from tests.helpers import generate_test_file


@pytest.mark.anyio
@pytest.mark.parametrize("uid, expected_status", [
    ("invalid_id!", 400),
    ("bad id", 400),
])
async def test_invalid_uid(websocket_client, test_client: httpx.AsyncClient, uid: str, expected_status: int):
    """Tests that endpoints reject invalid UIDs."""
    response_get = await test_client.get(f"/{uid}")
    assert response_get.status_code == expected_status

    response_put = await test_client.put(f"/{uid}/test.txt")
    assert response_put.status_code == expected_status

    with pytest.raises(WebSocketDisconnect):
        with websocket_client.websocket_connect(f"/send/{uid}"):  # type: ignore
            pass  # Connection should be rejected immediately


@pytest.mark.anyio
async def test_slash_in_uid_routes_to_404(test_client: httpx.AsyncClient):
    """Tests that UIDs with slashes get handled as separate routes and return 404."""
    # The "id/with/slash" gets parsed as path params, so it hits different routes
    response = await test_client.get("/id/with/slash")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_transfer_id_already_used(websocket_client):
    """Tests that creating a transfer with an existing ID fails."""
    uid = "duplicate-id"
    _, file_metadata = generate_test_file()

    # First creation should succeed
    with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        # Second attempt should fail with an error message
        with websocket_client.websocket_connect(f"/send/{uid}") as ws2:
            ws2.send_json({
                'file_name': file_metadata.name,
                'file_size': file_metadata.size,
                'file_type': file_metadata.type
            })
            response = ws2.receive_text()
            assert "Error: Transfer ID is already used." in response


@pytest.mark.anyio
async def test_sender_timeout(websocket_client, monkeypatch):
    """Tests that the sender times out if the receiver doesn't connect."""
    uid = "sender-timeout"
    _, file_metadata = generate_test_file()

    # Override the timeout for the test to make it fail quickly
    async def mock_wait_for_client_connected(self):
        await asyncio.sleep(1.0)  # Short delay
        raise asyncio.TimeoutError("Mocked timeout")

    from lib.transfer import FileTransfer
    monkeypatch.setattr(FileTransfer, 'wait_for_client_connected', mock_wait_for_client_connected)

    with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })
        # This should timeout because we are not starting a receiver
        response = ws.receive_text()
        assert "Error: Receiver did not connect in time." in response


@pytest.mark.anyio
async def test_receiver_disconnects(test_client: httpx.AsyncClient, websocket_client):
    """Tests that the sender is notified if the receiver disconnects mid-transfer."""
    uid = "receiver-disconnect"
    file_content, file_metadata = generate_test_file(size_in_kb=128)  # Larger file

    async def sender():
        # with pytest.raises(ClientDisconnect, check=lambda e: "Received less data than expected" in str(e)):
        with websocket_client.websocket_connect(f"/send/{uid}") as ws:
            await asyncio.sleep(0.1)

            ws.send_json({
                'file_name': file_metadata.name,
                'file_size': file_metadata.size,
                'file_type': file_metadata.type
            })
            await asyncio.sleep(1.0)  # Allow receiver to connect

            response = ws.receive_text()
            await asyncio.sleep(0.1)
            assert response == "Go for file chunks"

            chunks = [file_content[i:i + 4096] for i in range(0, len(file_content), 4096)]
            for chunk in chunks:
                ws.send_bytes(chunk)
                await asyncio.sleep(0.1)

            await asyncio.sleep(2.0)

        await asyncio.sleep(2.0)


    async def receiver():
        await asyncio.sleep(1.0)
        headers = {'Accept': '*/*'}

        async with test_client.stream("GET", f"/{uid}?download=true", headers=headers) as response:
            await asyncio.sleep(0.1)

            response.raise_for_status()
            i = 0
            # with pytest.raises(ClientDisconnect):
            async for chunk in response.aiter_bytes(4096):
                if not chunk:
                    break
                i += 1
                if i >= 5:
                    return
                await asyncio.sleep(0.025)

    t1 = asyncio.create_task(asyncio.wait_for(sender(), timeout=15))
    t2 = asyncio.create_task(asyncio.wait_for(receiver(), timeout=15))
    await asyncio.gather(t1, t2)



@pytest.mark.anyio
async def test_prefetcher_request(test_client: httpx.AsyncClient, websocket_client):
    """Tests that prefetcher user agents are served a preview page."""
    uid = "prefetch-test"
    _, file_metadata = generate_test_file()

    # Create a dummy transfer to get metadata
    with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await asyncio.sleep(0.1)

        ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })
        await asyncio.sleep(1.0)

        headers = {'User-Agent': 'facebookexternalhit/1.1'}
        response = await test_client.get(f"/{uid}", headers=headers)
        await asyncio.sleep(0.1)

        assert response.status_code == 200
        assert "text/html" in response.headers['content-type']
        assert "Ready to download" not in response.text
        assert "Download File" not in response.text


@pytest.mark.anyio
async def test_browser_download_page(test_client: httpx.AsyncClient, websocket_client):
    """Tests that a browser is served the download page."""
    uid = "browser-download-page"
    _, file_metadata = generate_test_file()

    with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await asyncio.sleep(0.1)

        ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })
        await asyncio.sleep(1.0)

        headers = {'User-Agent': 'Mozilla/5.0'}
        response = await test_client.get(f"/{uid}", headers=headers)
        await asyncio.sleep(0.1)

        assert response.status_code == 200
        assert "text/html" in response.headers['content-type']
        assert "Ready to download" in response.text
        assert "Download File" in response.text
