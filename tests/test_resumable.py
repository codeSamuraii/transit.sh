import anyio
import pytest
import httpx
from tests.helpers import generate_test_file
from tests.ws_client import WebSocketTestClient


@pytest.mark.anyio
async def test_http_resumable_download(test_client: httpx.AsyncClient, websocket_client: WebSocketTestClient):
    """Test that HTTP downloads can be resumed after disconnection."""
    uid = "test-resume-http"
    file_content, file_metadata = generate_test_file(size_in_kb=256)

    # Start the sender
    async def sender():
        async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
            await ws.send_json({
                'file_name': file_metadata.name,
                'file_size': file_metadata.size,
                'file_type': file_metadata.type
            })

            response = await ws.recv()
            assert response == "Go for file chunks"

            # Send file in chunks slowly to allow resume
            chunk_size = 8192
            for i in range(0, len(file_content), chunk_size):
                chunk = file_content[i:i + chunk_size]
                await ws.send_bytes(chunk)
                await anyio.sleep(0.05)  # Slower sending to allow resume

            # Send completion marker
            await ws.send_bytes(b'')
            await anyio.sleep(1)  # Keep connection open

    # Start receiver that will disconnect and resume
    async def receiver():
        await anyio.sleep(0.5)  # Let sender start

        received_bytes = b""

        # First download - disconnect after 25% of the file
        async with test_client.stream("GET", f"/{uid}?download=true") as response:
            assert response.status_code == 200
            bytes_to_receive = file_metadata.size // 4

            async for chunk in response.aiter_bytes(4096):
                received_bytes += chunk
                if len(received_bytes) >= bytes_to_receive:
                    break

        first_download_size = len(received_bytes)
        assert first_download_size >= bytes_to_receive

        await anyio.sleep(0.2)  # Small pause before resuming

        # Resume download
        async with test_client.stream("GET", f"/{uid}?download=true") as response:
            # Should get 206 Partial Content for resume
            assert response.status_code == 206

            # Check Content-Range header
            assert 'content-range' in response.headers

            async for chunk in response.aiter_bytes(4096):
                received_bytes += chunk

        # Verify we received the complete file
        assert len(received_bytes) == file_metadata.size
        assert received_bytes == file_content

    async with anyio.create_task_group() as tg:
        tg.start_soon(sender)
        tg.start_soon(receiver)


@pytest.mark.anyio
async def test_multiple_resume_attempts(test_client: httpx.AsyncClient, websocket_client: WebSocketTestClient):
    """Test that transfers can be resumed multiple times."""
    uid = "test-multi-resume"
    file_content, file_metadata = generate_test_file(size_in_kb=128)

    # Start the sender
    async def sender():
        async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
            await ws.send_json({
                'file_name': file_metadata.name,
                'file_size': file_metadata.size,
                'file_type': file_metadata.type
            })

            response = await ws.recv()
            assert response == "Go for file chunks"

            # Send file slowly to allow multiple resume attempts
            chunk_size = 4096
            for i in range(0, len(file_content), chunk_size):
                chunk = file_content[i:i + chunk_size]
                await ws.send_bytes(chunk)
                await anyio.sleep(0.02)

            await ws.send_bytes(b'')
            await anyio.sleep(2)

    # Receiver with multiple disconnects
    async def receiver():
        await anyio.sleep(0.5)

        received_bytes = b""
        download_attempts = [0.2, 0.4, 0.6, 1.0]  # Download percentages

        for attempt_idx, target_percentage in enumerate(download_attempts):
            target_bytes = int(file_metadata.size * target_percentage)

            async with test_client.stream("GET", f"/{uid}?download=true") as response:
                # First attempt gets 200, resumes get 206
                if attempt_idx == 0:
                    assert response.status_code == 200
                else:
                    assert response.status_code == 206

                async for chunk in response.aiter_bytes(4096):
                    received_bytes += chunk

                    # Stop at target percentage (except last attempt)
                    if target_percentage < 1.0 and len(received_bytes) >= target_bytes:
                        break

            if target_percentage < 1.0:
                await anyio.sleep(0.1)  # Pause between attempts

        # Verify complete file received
        assert len(received_bytes) == file_metadata.size
        assert received_bytes == file_content

    async with anyio.create_task_group() as tg:
        tg.start_soon(sender)
        tg.start_soon(receiver)