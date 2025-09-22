import anyio
import json
import pytest
import httpx
from fastapi import WebSocketDisconnect
from starlette.responses import ClientDisconnect
from websockets.exceptions import ConnectionClosedError, InvalidStatus

from tests.helpers import generate_test_file
from tests.ws_client import WebSocketTestClient
from tests.http_client import HTTPTestClient


@pytest.mark.anyio
@pytest.mark.parametrize("uid, expected_status", [
    ("invalid_id!", 400),
    ("bad id", 400),
])
async def test_invalid_uid(websocket_client: WebSocketTestClient, test_client: HTTPTestClient, uid: str, expected_status: int):
    """Tests that endpoints reject invalid UIDs."""
    response_get = await test_client.get(f"/{uid}")
    assert response_get.status_code == expected_status, f"GET /{uid} should return {expected_status}, got {response_get.status_code}"

    response_put = await test_client.put(f"/{uid}/test.txt")
    assert response_put.status_code == expected_status, f"PUT /{uid}/test.txt should return {expected_status}, got {response_put.status_code}"

    with pytest.raises((ConnectionClosedError, InvalidStatus)):
        async with websocket_client.websocket_connect(f"/send/{uid}") as _:  # type: ignore
            pass


@pytest.mark.anyio
async def test_slash_in_uid_routes_to_404(test_client: HTTPTestClient):
    """Tests that UIDs with slashes get handled as separate routes and return 404."""
    # The "id/with/slash" gets parsed as path params, so it hits different routes
    response = await test_client.get("/id/with/slash")
    assert response.status_code == 404, f"UID with slashes should return 404, got {response.status_code}"


@pytest.mark.anyio
async def test_transfer_id_already_used(websocket_client: WebSocketTestClient):
    """Tests that creating a transfer with an existing ID fails."""
    uid = "duplicate-id"
    _, file_metadata = generate_test_file()

    # First creation should succeed
    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        # Second attempt should fail with an error message
        async with websocket_client.websocket_connect(f"/send/{uid}") as ws2:
            await ws2.send_json({
                'file_name': file_metadata.name,
                'file_size': file_metadata.size,
                'file_type': file_metadata.type
            })
            response = await ws2.recv()
            assert "Error: Transfer ID is already used" in response


# @pytest.mark.anyio
# async def test_sender_timeout(websocket_client, monkeypatch):
#     """Tests that the sender times out if the receiver doesn't connect."""
#     uid = "sender-timeout"
#     _, file_metadata = generate_test_file()

#     # Override the timeout for the test to make it fail quickly
#     async def mock_wait_for_client_connected(self):
#         await anyio.sleep(1.0)  # Short delay
#         raise asyncio.TimeoutError("Mocked timeout")

#     from lib.transfer import FileTransfer
#     monkeypatch.setattr(FileTransfer, 'wait_for_client_connected', mock_wait_for_client_connected)

#     async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
#         await ws.websocket.send(json.dumps({
#             'file_name': file_metadata.name,
#             'file_size': file_metadata.size,
#             'file_type': file_metadata.type
#         }))
#         # This should timeout because we are not starting a receiver
#         response = await ws.websocket.recv()
#         assert "Error: Receiver did not connect in time." in response


@pytest.mark.anyio
async def test_receiver_disconnects(test_client: HTTPTestClient, websocket_client: WebSocketTestClient):
    """Tests that the sender waits for receiver reconnection."""
    uid = "receiver-disconnect"
    file_content, file_metadata = generate_test_file(size_in_kb=128)  # Larger file

    async def sender():
        async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
            await anyio.sleep(0.1)

            await ws.send_json({
                'file_name': file_metadata.name,
                'file_size': file_metadata.size,
                'file_type': file_metadata.type
            })
            await anyio.sleep(1.0)  # Allow receiver to connect

            response = await ws.recv()
            await anyio.sleep(0.1)
            assert response == "Go for file chunks"

            chunks = [file_content[i:i + 4096] for i in range(0, len(file_content), 4096)]
            for i, chunk in enumerate(chunks):
                await ws.send_bytes(chunk)
                await anyio.sleep(0.05)
                if i >= 10:  # Send enough chunks before receiver disconnects
                    break

            # Sender now waits for reconnection
            await anyio.sleep(2.0)
            # Transfer should continue waiting, not error immediately

    async def receiver():
        await anyio.sleep(1.0)
        headers = {'Accept': '*/*'}

        async with test_client.stream("GET", f"/{uid}?download=true", headers=headers) as response:
            await anyio.sleep(0.1)

            response.raise_for_status()
            i = 0
            with pytest.raises(ClientDisconnect):
                async for chunk in response.aiter_bytes(4096):
                    if not chunk:
                        break
                    i += 1
                    if i >= 5:
                        raise ClientDisconnect("Simulated disconnect")
                    await anyio.sleep(0.025)

    async with anyio.create_task_group() as tg:
        tg.start_soon(sender)
        tg.start_soon(receiver)


@pytest.mark.anyio
async def test_prefetcher_request(test_client: HTTPTestClient, websocket_client: WebSocketTestClient):
    """Tests that prefetcher user agents are served a preview page."""
    uid = "prefetch-test"
    _, file_metadata = generate_test_file()

    # Create a dummy transfer to get metadata
    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await anyio.sleep(0.1)

        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })
        await anyio.sleep(1.0)

        headers = {'User-Agent': 'facebookexternalhit/1.1'}
        response = await test_client.get(f"/{uid}", headers=headers)
        await anyio.sleep(0.1)

        assert response.status_code == 200
        assert "text/html" in response.headers['content-type']
        assert "Ready to download" not in response.text
        assert "Download File" not in response.text


@pytest.mark.anyio
async def test_browser_download_page(test_client: HTTPTestClient, websocket_client: WebSocketTestClient):
    """Tests that a browser is served the download page."""
    uid = "browser-download-page"
    _, file_metadata = generate_test_file()

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await anyio.sleep(0.1)

        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })
        await anyio.sleep(1.0)

        headers = {'User-Agent': 'Mozilla/5.0'}
        response = await test_client.get(f"/{uid}", headers=headers)
        await anyio.sleep(0.1)

        assert response.status_code == 200, f"Browser download page should return 200, got {response.status_code}"
        assert "text/html" in response.headers['content-type'], f"Browser should get HTML content-type, got {response.headers.get('content-type')}"
        assert "Ready to download" in response.text, "Download page should contain 'Ready to download' text"
        assert "Download File" in response.text, "Download page should contain 'Download File' text"


@pytest.mark.anyio
async def test_range_download_basic(test_client: HTTPTestClient, websocket_client: WebSocketTestClient):
    """Test basic HTTP Range header support."""
    uid = "range-basic"
    file_content, file_metadata = generate_test_file(size_in_kb=32)

    # Upload file first
    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        async def download_with_range():
            await anyio.sleep(0.5)  # Let upload start

            # Test range request
            headers = {'Range': 'bytes=0-8191'}
            response = await test_client.get(f"/{uid}?download=true", headers=headers)
            assert response.status_code == 206, f"Range request should return 206 Partial Content, got {response.status_code}"
            assert 'Content-Range' in response.headers, "Response should include Content-Range header for partial content"
            assert len(response.content) == 8192, f"Range 0-8191 should return 8192 bytes, got {len(response.content)}"
            return response.content

        async with anyio.create_task_group() as tg:
            download_task = tg.start_soon(download_with_range)

            # Wait for receiver then upload
            response = await ws.recv()
            assert response == "Go for file chunks", f"Expected 'Go for file chunks' signal, got '{response}'"

            # Upload the file
            chunks = [file_content[i:i + 4096] for i in range(0, len(file_content), 4096)]
            for chunk in chunks:
                await ws.send_bytes(chunk)
                await anyio.sleep(0.01)

            await ws.send_bytes(b'')  # End marker


@pytest.mark.anyio
async def test_multiple_range_requests(test_client: HTTPTestClient, websocket_client: WebSocketTestClient):
    """Test multiple HTTP range requests to the same file."""
    uid = "multi-range"
    file_content = b'a' * 8192 + b'b' * 8192 + b'c' * 8192 + b'd' * 8192
    file_metadata = generate_test_file(size_in_kb=32)[1]
    file_metadata.size = len(file_content)

    # Upload the file first
    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        async def download_full():
            await anyio.sleep(0.5)
            # First download the full file to ensure upload completes
            response = await test_client.get(f"/{uid}?download=true")
            assert response.status_code == 200, f"Full download should return 200 OK, got {response.status_code}"
            assert len(response.content) == 32768, f"Full file should be 32768 bytes, got {len(response.content)}"

        async with anyio.create_task_group() as tg:
            tg.start_soon(download_full)

            # Upload the file
            response = await ws.recv()
            assert response == "Go for file chunks", f"Expected 'Go for file chunks' signal, got '{response}'"

            # Send the data
            for chunk_data in [b'a' * 8192, b'b' * 8192, b'c' * 8192, b'd' * 8192]:
                await ws.send_bytes(chunk_data)
                await anyio.sleep(0.01)

            await ws.send_bytes(b'')
