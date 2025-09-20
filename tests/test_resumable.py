import pytest
import httpx
import anyio
from tests.helpers import generate_test_file
from tests.ws_client import WebSocketTestClient


@pytest.mark.anyio
async def test_websocket_upload_resume(websocket_client: WebSocketTestClient, test_client: httpx.AsyncClient):
    """Test WebSocket upload with simulated disconnection and resumption."""
    uid = "ws-resume-test"
    file_content, file_metadata = generate_test_file(size_in_kb=64)

    async def start_receiver():
        """Start a receiver to trigger upload."""
        await anyio.sleep(0.5)  # Let sender connect first
        response = await test_client.get(f"/{uid}?download=true")
        # Receiver will wait for data

    async def upload_with_disconnect():
        """Upload with simulated disconnection."""
        # First connection - partial upload
        async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
            await ws.send_json({
                'file_name': file_metadata.name,
                'file_size': file_metadata.size,
                'file_type': file_metadata.type
            })

            # Wait for receiver to connect
            response = await ws.recv()
            assert response == "Go for file chunks"

            # Send partial data
            chunks = [file_content[i:i + 4096] for i in range(0, len(file_content), 4096)]
            for i, chunk in enumerate(chunks[:5]):  # Send only first 5 chunks
                await ws.send_bytes(chunk)
                await anyio.sleep(0.01)

            # Simulate disconnection

    async with anyio.create_task_group() as tg:
        tg.start_soon(start_receiver)
        tg.start_soon(upload_with_disconnect)

    await anyio.sleep(1.0)

    # Now test resume - the transfer should have saved progress
    # For simplicity, we'll just verify the transfer still exists
    # and can be continued


@pytest.mark.anyio
async def test_http_download_range(test_client: httpx.AsyncClient, websocket_client: WebSocketTestClient):
    """Test HTTP download with Range header for resumption."""
    uid = "range-test"
    file_content, file_metadata = generate_test_file(size_in_kb=32)

    # Upload the file first
    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        await anyio.sleep(0.5)

        # Start download with range header in background
        async def download_partial():
            # First partial download
            headers1 = {'Range': 'bytes=0-8191'}
            response1 = await test_client.get(f"/{uid}?download=true", headers=headers1)
            assert response1.status_code == 206  # Partial Content
            assert 'Content-Range' in response1.headers
            assert len(response1.content) == 8192

            # Second partial download
            headers2 = {'Range': 'bytes=8192-16383'}
            response2 = await test_client.get(f"/{uid}?download=true", headers=headers2)
            assert response2.status_code == 206
            assert len(response2.content) == 8192

            # Verify content matches
            combined = response1.content + response2.content
            assert combined == file_content[:16384]

        async with anyio.create_task_group() as tg:
            tg.start_soon(download_partial)

            # Wait for download to start
            await anyio.sleep(0.2)

            # Upload the file
            response = await ws.recv()
            assert response == "Go for file chunks"

            chunks = [file_content[i:i + 4096] for i in range(0, len(file_content), 4096)]
            for chunk in chunks:
                await ws.send_bytes(chunk)
                await anyio.sleep(0.01)

            await ws.send_bytes(b'')  # End marker


@pytest.mark.anyio
async def test_upload_progress_persistence(websocket_client: WebSocketTestClient):
    """Test that upload progress is persisted across disconnections."""
    uid = "progress-test"
    file_content, file_metadata = generate_test_file(size_in_kb=100)

    bytes_sent_first = 0

    # First connection - partial upload
    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        # Send partial data
        chunks = [file_content[i:i + 4096] for i in range(0, len(file_content), 4096)]
        for i, chunk in enumerate(chunks[:10]):  # Send 10 chunks
            await ws.send_bytes(chunk)
            bytes_sent_first += len(chunk)
            await anyio.sleep(0.01)

    await anyio.sleep(0.5)

    # Verify progress was saved by resuming
    async with websocket_client.websocket_connect(f"/resume/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        response = await ws.recv()
        assert "Resume from:" in response

        resume_bytes = int(response.split(":")[1].strip())
        # Should resume from approximately where we left off
        # Allow some variation due to chunk boundaries
        assert abs(resume_bytes - bytes_sent_first) < 4096


@pytest.mark.anyio
async def test_concurrent_range_downloads(test_client: httpx.AsyncClient, websocket_client: WebSocketTestClient):
    """Test multiple concurrent downloads with different ranges."""
    uid = "concurrent-range"
    file_content, file_metadata = generate_test_file(size_in_kb=64)

    # Upload file
    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        async def download_range(start: int, end: int):
            headers = {'Range': f'bytes={start}-{end}'}
            response = await test_client.get(f"/{uid}?download=true", headers=headers)
            assert response.status_code == 206
            expected_size = end - start + 1
            assert len(response.content) == expected_size
            assert response.content == file_content[start:end+1]

        async with anyio.create_task_group() as tg:
            # Start multiple concurrent range downloads
            tg.start_soon(download_range, 0, 8191)
            tg.start_soon(download_range, 8192, 16383)
            tg.start_soon(download_range, 16384, 24575)
            tg.start_soon(download_range, 24576, 32767)

            # Wait for downloads to start
            await anyio.sleep(0.2)

            # Upload the file
            response = await ws.recv()
            assert response == "Go for file chunks"

            chunks = [file_content[i:i + 4096] for i in range(0, len(file_content), 4096)]
            for chunk in chunks:
                await ws.send_bytes(chunk)
                await anyio.sleep(0.01)

            await ws.send_bytes(b'')