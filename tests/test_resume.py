from turtle import ht
import anyio
import json
import pytest
import httpx

from tests.helpers import generate_test_file
from tests.ws_client import WebSocketTestClient


@pytest.mark.anyio
async def test_resume_upload_success(websocket_client: WebSocketTestClient, test_client: httpx.AsyncClient):
    """Test successful upload resumption after disconnection."""
    uid = "resume-upload-success"
    file_content, file_metadata = generate_test_file(size_in_kb=64)

    async def sender():
        # Start initial upload
        async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
            await ws.send_json({
                'file_name': file_metadata.name,
                'file_size': file_metadata.size,
                'file_type': file_metadata.type
            })

            await anyio.sleep(0.5)
            response = await ws.recv()
            assert response == "Go for file chunks", f"Expected 'Go for file chunks', got '{response}'"

            # Send first 24KB (6 chunks of 4KB each)
            chunks_sent = 0
            bytes_sent = 0
            for i in range(0, 24576, 4096):
                chunk = file_content[i:i+4096]
                await ws.send_bytes(chunk)
                chunks_sent += 1
                bytes_sent += len(chunk)
                await anyio.sleep(0.05)

            # Disconnect abruptly

        await anyio.sleep(1.5)

        # Resume the upload
        async with websocket_client.websocket_connect(f"/resume/{uid}") as ws:
            await ws.send_json({
                'file_name': file_metadata.name,
                'file_size': file_metadata.size,
                'file_type': file_metadata.type
            })

            await anyio.sleep(0.5)
            response = await ws.recv()
            assert "Resume from:" in response, f"Expected 'Resume from:' message, got '{response}'"

            resume_position = int(response.split(":")[1].strip())
            assert resume_position > 0, f"Resume position should be > 0, got {resume_position}"
            assert resume_position <= bytes_sent, f"Resume position {resume_position} should not exceed bytes sent {bytes_sent}"

            # Complete the upload from resume position
            remaining_data = file_content[resume_position:]
            for i in range(0, len(remaining_data), 4096):
                chunk = remaining_data[i:i+4096]
                await ws.send_bytes(chunk)
                await anyio.sleep(0.01)

            await ws.send_bytes(b'')  # End marker

        await anyio.sleep(0.5)

    async with anyio.create_task_group() as tg:
        receiver_task = tg.start_soon(sender)
        await anyio.sleep(1)  # Ensure sender starts first
        # Verify the complete file can be downloaded
        response = await test_client.get(f"/{uid}?download=true")
        assert response.status_code == 200, f"Download should succeed, got status {response.status_code}"
        assert len(response.content) == file_metadata.size, f"Downloaded size should be {file_metadata.size}, got {len(response.content)}"
        assert response.content == file_content, "Downloaded content should match original file"


@pytest.mark.anyio
async def test_resume_upload_metadata_mismatch(websocket_client: WebSocketTestClient, test_client: httpx.AsyncClient):
    """Test that resume fails when file metadata doesn't match."""
    uid = "resume-metadata-mismatch"
    file_content, file_metadata = generate_test_file(size_in_kb=32)

    # Create initial transfer
    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        async def download():
            await anyio.sleep(0.5)

            try:
                async with test_client.stream("GET", f"/{uid}?download=true", timeout=3) as response:
                    async for chunk in response.aiter_bytes(4096):
                        await anyio.sleep(0.1)
                        pass
            except httpx.ReadTimeout:
                pass  # Expected if upload is incomplete

        async with anyio.create_task_group() as tg:
            tg.start_soon(download)

            response = await ws.recv()
            assert response == "Go for file chunks", f"Expected 'Go for file chunks', got '{response}'"
            await ws.send_bytes(file_content[:8192])  # Send first 8KB
            await anyio.sleep(0.1)

            # Try to resume with different metadata
            async with websocket_client.websocket_connect(f"/resume/{uid}") as ws:
                await ws.send_json({
                    'file_name': "different.txt",  # Different name
                    'file_size': file_metadata.size,
                    'file_type': file_metadata.type
                })

                response = await ws.recv()
                assert "Error:" in response, f"Expected error for metadata mismatch, got '{response}'"
                assert "does not match" in response.lower(), f"Error should mention mismatch, got '{response}'"


@pytest.mark.anyio
async def test_resume_upload_nonexistent_transfer(websocket_client: WebSocketTestClient):
    """Test that resume fails when transfer doesn't exist."""
    uid = "resume-nonexistent"
    _, file_metadata = generate_test_file(size_in_kb=32)

    async with websocket_client.websocket_connect(f"/resume/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        response = await ws.recv()
        assert "Error:" in response, f"Expected error for nonexistent transfer, got '{response}'"
        assert ("not found" in response.lower() or "does not exist" in response.lower()), f"Error should mention transfer not found, got '{response}'"


@pytest.mark.anyio
async def test_resume_upload_completed_transfer(websocket_client: WebSocketTestClient, test_client: httpx.AsyncClient):
    """Test that resume fails when transfer is already completed."""
    uid = "resume-completed"
    file_content, file_metadata = generate_test_file(size_in_kb=8)  # Small file for quick transfer

    # Complete a transfer
    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        async def download():
            await anyio.sleep(0.5)
            response = await test_client.get(f"/{uid}?download=true")
            assert response.status_code == 200, f"Download should succeed, got status {response.status_code}"
            return response.content

        async with anyio.create_task_group() as tg:
            tg.start_soon(download)

            response = await ws.recv()
            assert response == "Go for file chunks", f"Expected 'Go for file chunks', got '{response}'"

            # Send complete file
            await ws.send_bytes(file_content)
            await anyio.sleep(0.1)
            await ws.send_bytes(b'')  # End marker

    await anyio.sleep(1.5)

    # Try to resume completed transfer
    async with websocket_client.websocket_connect(f"/resume/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        response = await ws.recv()
        assert "Error:" in response, f"Expected error for completed transfer, got '{response}'"


@pytest.mark.anyio
async def test_resume_upload_multiple_times(websocket_client: WebSocketTestClient, test_client: httpx.AsyncClient):
    """Test that upload can be resumed multiple times."""
    uid = "resume-multiple"
    file_content, file_metadata = generate_test_file(size_in_kb=128)

    # First upload attempt - send 20KB
    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_json({
            'file_name': file_metadata.name,
            'file_size': file_metadata.size,
            'file_type': file_metadata.type
        })

        async def receiver1():
            await anyio.sleep(0.5)
            async with test_client.stream("GET", f"/{uid}?download=true") as response:
                downloaded = b''
                async for chunk in response.aiter_bytes(4096):
                    downloaded += chunk

                assert response.status_code == 200, f"Final download should succeed, got status {response.status_code}"
                assert len(downloaded) == file_metadata.size, f"Downloaded size should be {file_metadata.size}, got {len(downloaded)}"
                assert downloaded == file_content, "Downloaded content should match original file"

        async with anyio.create_task_group() as tg:
            tg.start_soon(receiver1)

            response = await ws.recv()
            assert response == "Go for file chunks", f"Expected 'Go for file chunks', got '{response}'"

            for i in range(0, 20480, 4096):
                await ws.send_bytes(file_content[i:i+4096])
                await anyio.sleep(0.05)

            await anyio.sleep(0.5)

            # Second upload attempt - resume and send another 20KB
            resume_pos1 = 0
            async with websocket_client.websocket_connect(f"/resume/{uid}") as ws:
                await ws.send_json({
                    'file_name': file_metadata.name,
                    'file_size': file_metadata.size,
                    'file_type': file_metadata.type
                })

                response = await ws.recv()
                assert "Resume from:" in response, f"Expected 'Resume from:' message, got '{response}'"
                resume_pos1 = int(response.split(":")[1].strip())
                assert resume_pos1 >= 16384, f"First resume position should be at least 16KB, got {resume_pos1}"

                for i in range(resume_pos1, min(resume_pos1 + 20480, file_metadata.size), 4096):
                    await ws.send_bytes(file_content[i:i+4096])
                    await anyio.sleep(0.05)

            await anyio.sleep(0.5)

            # Third upload attempt - complete the transfer
            async with websocket_client.websocket_connect(f"/resume/{uid}") as ws:
                await ws.send_json({
                    'file_name': file_metadata.name,
                    'file_size': file_metadata.size,
                    'file_type': file_metadata.type
                })

                response = await ws.recv()
                assert "Resume from:" in response, f"Expected 'Resume from:' message, got '{response}'"
                resume_pos2 = int(response.split(":")[1].strip())
                assert resume_pos2 > resume_pos1, f"Second resume position {resume_pos2} should be greater than first {resume_pos1}"

                # Complete the upload
                remaining = file_content[resume_pos2:]
                for i in range(0, len(remaining), 4096):
                    await ws.send_bytes(remaining[i:i+4096])
                    await anyio.sleep(0.01)

                await ws.send_bytes(b'')  # End marker
