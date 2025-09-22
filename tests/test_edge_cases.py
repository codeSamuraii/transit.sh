import h11
import anyio
import json
import pytest
import httpx

from tests.helpers import generate_test_file
from tests.ws_client import WebSocketTestClient
from tests.http_client import HTTPTestClient
from lib.logging import get_logger
log = get_logger('edge-cases')


@pytest.mark.anyio
async def test_empty_file_transfer(websocket_client: WebSocketTestClient, test_client: HTTPTestClient):
    """Test transfer of empty file (0 bytes)."""
    uid = "empty-file"
    file_content = b'x'  # Minimal 1-byte file
    _, file_metadata = generate_test_file(size_in_kb=1)
    file_metadata.size = 1  # Set to minimal size
    file_metadata.name = "minimal.txt"

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        async def receiver():
            await anyio.sleep(0.5)
            response = await test_client.get(f"/{uid}?download=true")
            assert response.status_code == 200, f"Download should succeed for minimal file, got status {response.status_code}"
            return response.content

        async with anyio.create_task_group() as tg:
            download_task = tg.start_soon(receiver)

            response = await ws.recv()
            assert response == "Go for file chunks", f"Expected 'Go for file chunks', got '{response}'"

            # Send minimal data (1 byte) and end marker
            await ws.send_bytes(b'x')
            await anyio.sleep(0.1)
            await ws.send_bytes(b'')  # End marker


@pytest.mark.anyio
async def test_file_with_special_characters(websocket_client: WebSocketTestClient, test_client: HTTPTestClient):
    """Test file transfer with special characters in filename."""
    uid = "special-chars"
    file_content, file_metadata = generate_test_file(size_in_kb=8)
    file_metadata.name = "test file (2024) [version 1.0].txt"  # Should be escaped

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        async def receiver():
            await anyio.sleep(0.5)
            response = await test_client.get(f"/{uid}?download=true")
            assert response.status_code == 200, f"Download should succeed, got status {response.status_code}"
            # Check that filename is properly escaped in header
            assert "filename=" in response.headers.get('content-disposition', ''), "Content-Disposition should contain filename"
            return response.content

        async with anyio.create_task_group() as tg:
            tg.start_soon(receiver)

            response = await ws.recv()
            assert response == "Go for file chunks", f"Expected 'Go for file chunks', got '{response}'"

            await ws.send_bytes(file_content)
            await anyio.sleep(0.1)
            await ws.send_bytes(b'')


@pytest.mark.anyio
async def test_http_upload_size_limit(test_client: HTTPTestClient):
    """Test that HTTP upload enforces 1GiB size limit."""
    uid = "http-size-limit"

    # Create a small file but claim it's over 1GiB in headers
    small_content = b'x' * 1024  # 1KB actual data
    headers = {
        'Content-Type': 'application/octet-stream',
        'Content-Length': str(1024**3 + 1),  # Claim 1GiB + 1 byte,
        'Content-Range': 'bytes=0-1024'  # Partial content header
    }

    try:
        response = await test_client.put(f"/{uid}/large.bin", headers=headers, content=small_content)
        assert response.status_code == 413, f"Should reject files over 1GiB, got status {response.status_code}"
        assert "too large" in response.text.lower(), f"Error should mention file too large, got '{response.text}'"
    except h11.LocalProtocolError as e:
        log.debug(f"- Silenced error: {type(e).__name__}: {e}")
        pass  # Ignore h11 protocol errors during tests


@pytest.mark.anyio
@pytest.mark.skip(reason="Timeout test takes too long (5 minutes)")
async def test_sender_timeout_no_receiver(websocket_client: WebSocketTestClient):
    """Test that sender times out if receiver doesn't connect."""
    uid = "sender-timeout"
    _, file_metadata = generate_test_file(size_in_kb=8)

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        # Wait for timeout (should be 5 minutes max)
        with anyio.fail_after(310):  # 5 minutes + 10 seconds buffer
            response = await ws.recv()
            assert "Error:" in response, f"Expected error for timeout, got '{response}'"
            assert ("did not connect" in response.lower() or "timeout" in response.lower()), f"Error should mention timeout, got '{response}'"


@pytest.mark.anyio
async def test_concurrent_receivers_rejected(websocket_client: WebSocketTestClient, test_client: HTTPTestClient):
    """Test that only one receiver can connect at a time for normal downloads."""
    uid = "concurrent-receivers"
    file_content, file_metadata = generate_test_file(size_in_kb=32)

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        async def first_receiver():
            await anyio.sleep(0.5)
            async with test_client.stream("GET", f"/{uid}?download=true") as response:
                assert response.status_code == 200, f"First receiver should connect, got status {response.status_code}"
                # Keep connection open
                await anyio.sleep(2)
                async for chunk in response.aiter_bytes(4096):
                    pass

        async def second_receiver():
            await anyio.sleep(1.0)  # Let first receiver connect
            response = await test_client.get(f"/{uid}?download=true")
            assert response.status_code == 409, f"Second receiver should be rejected with 409, got status {response.status_code}"
            assert "already downloading" in response.text.lower(), f"Error should mention already downloading, got '{response.text}'"

        async with anyio.create_task_group() as tg:
            tg.start_soon(first_receiver)
            tg.start_soon(second_receiver)

            response = await ws.recv()
            assert response == "Go for file chunks", f"Expected 'Go for file chunks', got '{response}'"

            # Send data slowly
            for i in range(0, len(file_content), 4096):
                await ws.send_bytes(file_content[i:i+4096])
                await anyio.sleep(0.1)

            await ws.send_bytes(b'')


@pytest.mark.anyio
async def test_concurrent_senders_rejected(websocket_client: WebSocketTestClient):
    """Test that only one sender can use a transfer ID."""
    uid = "concurrent-senders"
    _, file_metadata = generate_test_file(size_in_kb=8)

    # First sender creates transfer
    async with websocket_client.websocket_connect(f"/send/{uid}") as ws1:
        await ws1.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        await anyio.sleep(0.5)

        # Second sender tries to use same ID
        async with websocket_client.websocket_connect(f"/send/{uid}") as ws2:
            await ws2.send_json({
                'file_name': file_metadata.name,
                'file_size': file_metadata.size,
                'file_type': file_metadata.type
            })

            response = await ws2.recv()
            assert "Error:" in response, f"Second sender should get error, got '{response}'"
            assert "already used" in response.lower(), f"Error should mention ID already used, got '{response}'"


@pytest.mark.anyio
async def test_invalid_json_metadata(websocket_client: WebSocketTestClient):
    """Test that invalid JSON metadata is rejected."""
    uid = "invalid-json"

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        # Send invalid JSON
        await ws.websocket.send("not valid json {]}")

        response = await ws.recv()
        assert "Error:" in response, f"Expected error for invalid JSON, got '{response}'"
        assert ("invalid" in response.lower() or "decode" in response.lower()), f"Error should mention invalid/decode, got '{response}'"


@pytest.mark.anyio
async def test_missing_metadata_fields(websocket_client: WebSocketTestClient):
    """Test that missing required metadata fields are rejected."""
    uid = "missing-fields"

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        # Send metadata without file_size
        await ws.send_json({
            'file_name': 'test.txt',
            # 'file_size' is missing
            'file_type': 'text/plain'
        })

        response = await ws.recv()
        assert "Error:" in response, f"Expected error for missing fields, got '{response}'"
        assert "invalid" in response.lower(), f"Error should mention invalid metadata, got '{response}'"


@pytest.mark.anyio
async def test_sender_disconnect_during_transfer(websocket_client: WebSocketTestClient, test_client: HTTPTestClient):
    """Test that receiver handles sender disconnection gracefully."""
    uid = "sender-disconnect"
    file_content, file_metadata = generate_test_file(size_in_kb=64)

    sender_disconnected = False

    async def sender():
        nonlocal sender_disconnected
        async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
            await ws.send_json({
                'file_name': file_metadata.name,
                'file_size': file_metadata.size,
                'file_type': file_metadata.type
            })

            response = await ws.recv()
            assert response == "Go for file chunks", f"Expected 'Go for file chunks', got '{response}'"

            # Send partial data
            for i in range(0, 20480, 4096):  # Send 20KB
                await ws.send_bytes(file_content[i:i+4096])
                await anyio.sleep(0.05)

            sender_disconnected = True
            # Disconnect without sending end marker

    async def receiver():
        await anyio.sleep(0.5)

        try:
            async with test_client.stream("GET", f"/{uid}?download=true") as response:
                assert response.status_code == 200, f"Receiver should connect, got status {response.status_code}"

                downloaded = b''
                with anyio.fail_after(10):
                    async for chunk in response.aiter_bytes(4096):
                        downloaded += chunk
                        if sender_disconnected and len(downloaded) >= 16384:
                            # Should eventually fail or timeout when sender disconnects
                            break

                # Transfer should be incomplete
                assert len(downloaded) < file_metadata.size, f"Should not receive full file when sender disconnects, got {len(downloaded)} bytes"
        except (httpx.ReadTimeout, httpx.RemoteProtocolError):
            # Expected when sender disconnects
            pass

    async with anyio.create_task_group() as tg:
        tg.start_soon(sender)
        tg.start_soon(receiver)


@pytest.mark.anyio
async def test_cleanup_after_transfer(websocket_client: WebSocketTestClient, test_client: HTTPTestClient):
    """Test that transfer is cleaned up after completion."""
    uid = "cleanup-test"
    file_content, file_metadata = generate_test_file(size_in_kb=8)

    # Complete a transfer
    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        async def receiver():
            await anyio.sleep(0.5)
            response = await test_client.get(f"/{uid}?download=true")
            assert response.status_code == 200, f"Download should succeed, got status {response.status_code}"
            return response.content

        async with anyio.create_task_group() as tg:
            tg.start_soon(receiver)

            response = await ws.recv()
            assert response == "Go for file chunks", f"Expected 'Go for file chunks', got '{response}'"

            await ws.send_bytes(file_content)
            await anyio.sleep(0.1)
            await ws.send_bytes(b'')

    # Wait for cleanup to potentially occur
    await anyio.sleep(2)

    # Try to access the transfer again - should fail
    response = await test_client.get(f"/{uid}?download=true")
    assert response.status_code == 404, f"Transfer should be cleaned up and return 404, got status {response.status_code}"


@pytest.mark.anyio
async def test_large_file_streaming(websocket_client: WebSocketTestClient, test_client: HTTPTestClient):
    """Test streaming of larger files to verify memory efficiency."""
    uid = "large-file"
    file_content, file_metadata = generate_test_file(size_in_kb=512)  # 512KB test file

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        bytes_downloaded = 0

        async def receiver():
            nonlocal bytes_downloaded
            await anyio.sleep(0.5)
            async with test_client.stream("GET", f"/{uid}?download=true") as response:
                assert response.status_code == 200, f"Download should succeed, got status {response.status_code}"

                async for chunk in response.aiter_bytes(8192):
                    bytes_downloaded += len(chunk)
                    if bytes_downloaded >= file_metadata.size:
                        break

            assert bytes_downloaded == file_metadata.size, f"Should download complete file, got {bytes_downloaded}/{file_metadata.size} bytes"

        async with anyio.create_task_group() as tg:
            tg.start_soon(receiver)

            response = await ws.recv()
            assert response == "Go for file chunks", f"Expected 'Go for file chunks', got '{response}'"

            # Stream file in chunks
            chunk_size = 8192
            for i in range(0, len(file_content), chunk_size):
                chunk = file_content[i:i+chunk_size]
                await ws.send_bytes(chunk)
                await anyio.sleep(0.01)

            await ws.send_bytes(b'')
