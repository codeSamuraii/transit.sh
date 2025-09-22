import anyio
import pytest

from tests.helpers import generate_test_file
from tests.ws_client import WebSocketTestClient
from tests.http_client import HTTPTestClient


@pytest.mark.anyio
async def test_range_request_start_end(test_client: HTTPTestClient, websocket_client: WebSocketTestClient):
    """Test basic range request with start and end bytes."""
    uid = "range-start-end"
    file_content = b'a' * 1024 + b'b' * 1024 + b'c' * 1024 + b'd' * 1024  # 4KB file
    file_metadata = generate_test_file(size_in_kb=4)[1]
    file_metadata.size = len(file_content)

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        async def test_range_download():
            await anyio.sleep(0.5)
            # Test specific range request (second KB, all 'b's)
            response = await test_client.download_with_range(uid, 1024, 2047)
            test_client.verify_range_response(response, 1024, 2047, b'b' * 1024)

        async with anyio.create_task_group() as tg:
            tg.start_soon(test_range_download)
            await ws.upload_with_metadata(file_content, file_metadata)


@pytest.mark.anyio
async def test_range_request_open_ended(test_client: HTTPTestClient, websocket_client: WebSocketTestClient):
    """Test open-ended range request (bytes=N-)."""
    uid = "range-open-ended"
    file_content, file_metadata = generate_test_file(size_in_kb=8)

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_file_metadata(file_metadata)

        async def download_range():
            await anyio.sleep(0.5)
            # Request from byte 6144 to end (last 2KB of 8KB file)
            headers = {'Range': 'bytes=6144-'}
            response = await test_client.get(f"/{uid}?download=true", headers=headers)
            assert response.status_code == 206, f"Open-ended range should return 206, got {response.status_code}"
            assert len(response.content) == 2048, f"Should get last 2KB, got {len(response.content)} bytes"

            content_range = response.headers.get('content-range')
            assert content_range == 'bytes 6144-8191/8192', f"Content-Range should be 'bytes 6144-8191/8192', got '{content_range}'"
            return response.content

        async with anyio.create_task_group() as tg:
            tg.start_soon(download_range)

            await ws.wait_for_go_signal()
            await ws.upload_file_chunks(file_content)


@pytest.mark.anyio
async def test_range_request_suffix(test_client: HTTPTestClient, websocket_client: WebSocketTestClient):
    """Test suffix range request (bytes=-N)."""
    uid = "range-suffix"
    file_content, file_metadata = generate_test_file(size_in_kb=8)

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_file_metadata(file_metadata)

        async def download_range():
            await anyio.sleep(0.5)
            # Request last 1024 bytes
            headers = {'Range': 'bytes=-1024'}
            response = await test_client.get(f"/{uid}?download=true", headers=headers)
            assert response.status_code == 206, f"Suffix range should return 206, got {response.status_code}"
            assert len(response.content) == 1024, f"Should get last 1KB, got {len(response.content)} bytes"

            content_range = response.headers.get('content-range')
            assert content_range == 'bytes 7168-8191/8192', f"Content-Range should be 'bytes 7168-8191/8192', got '{content_range}'"
            return response.content

        async with anyio.create_task_group() as tg:
            tg.start_soon(download_range)

            await ws.wait_for_go_signal()
            await ws.upload_file_chunks(file_content)


@pytest.mark.anyio
async def test_multiple_concurrent_ranges(test_client: HTTPTestClient, websocket_client: WebSocketTestClient):
    """Test multiple concurrent range requests to the same file."""
    uid = "multiple-ranges"
    # Create distinct data pattern: 0000111122223333
    file_content = b'0' * 1024 + b'1' * 1024 + b'2' * 1024 + b'3' * 1024
    file_metadata = generate_test_file(size_in_kb=4)[1]
    file_metadata.size = len(file_content)

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_file_metadata(file_metadata)

        results = {}

        async def download_range(start: int, end: int, key: str):
            await anyio.sleep(0.5)
            headers = {'Range': f'bytes={start}-{end}'}
            response = await test_client.get(f"/{uid}?download=true", headers=headers)
            assert response.status_code == 206, f"Range {key} should return 206, got {response.status_code}"
            results[key] = response.content

        async with anyio.create_task_group() as tg:
            # Start multiple concurrent range downloads
            tg.start_soon(download_range, 0, 1023, 'first')      # First KB
            tg.start_soon(download_range, 1024, 2047, 'second')  # Second KB
            tg.start_soon(download_range, 2048, 3071, 'third')   # Third KB
            tg.start_soon(download_range, 3072, 4095, 'fourth')  # Fourth KB

            response = await ws.recv()
            assert response == "Go for file chunks", f"Expected 'Go for file chunks', got '{response}'"

            # Upload the file
            await ws.send_bytes(file_content)
            await ws.send_bytes(b'')

        # Verify all ranges got correct data
        assert results['first'] == b'0' * 1024, "First range should get all '0's"
        assert results['second'] == b'1' * 1024, "Second range should get all '1's"
        assert results['third'] == b'2' * 1024, "Third range should get all '2's"
        assert results['fourth'] == b'3' * 1024, "Fourth range should get all '3's"


@pytest.mark.anyio
async def test_range_beyond_file_size(test_client: HTTPTestClient, websocket_client: WebSocketTestClient):
    """Test range request beyond file size."""
    uid = "range-beyond"
    file_content, file_metadata = generate_test_file(size_in_kb=4)

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_file_metadata(file_metadata)

        async def download_range():
            await anyio.sleep(0.5)
            # Request range starting beyond file size
            headers = {'Range': 'bytes=5000-6000'}  # File is only 4096 bytes
            response = await test_client.get(f"/{uid}?download=true", headers=headers)
            # Should return full file or 416 Range Not Satisfiable
            assert response.status_code in [200, 416], f"Range beyond file should return 200 or 416, got {response.status_code}"
            return response.status_code

        async with anyio.create_task_group() as tg:
            tg.start_soon(download_range)

            await ws.wait_for_go_signal()
            await ws.upload_file_chunks(file_content)


@pytest.mark.anyio
async def test_range_with_end_beyond_file(test_client: HTTPTestClient, websocket_client: WebSocketTestClient):
    """Test range request with end byte beyond file size."""
    uid = "range-end-beyond"
    file_content, file_metadata = generate_test_file(size_in_kb=4)

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_file_metadata(file_metadata)

        async def download_range():
            await anyio.sleep(0.5)
            # Request range with end beyond file size
            headers = {'Range': 'bytes=2048-8000'}  # File is only 4096 bytes
            response = await test_client.get(f"/{uid}?download=true", headers=headers)
            assert response.status_code == 206, f"Range with end beyond file should return 206, got {response.status_code}"
            assert len(response.content) == 2048, f"Should get from 2048 to end (2048 bytes), got {len(response.content)}"

            content_range = response.headers.get('content-range')
            assert content_range == 'bytes 2048-4095/4096', f"Content-Range should be 'bytes 2048-4095/4096', got '{content_range}'"
            return response.content

        async with anyio.create_task_group() as tg:
            tg.start_soon(download_range)

            await ws.wait_for_go_signal()
            await ws.upload_file_chunks(file_content)


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_range", [
    'bytes=abc-def',      # Non-numeric
    'kilobytes=0-1024',   # Wrong unit
    'bytes=1024-512',     # End before start
    'bytes',              # Missing range spec
    'bytes=',             # Empty range spec
])
async def test_invalid_range_header(test_client: HTTPTestClient, websocket_client: WebSocketTestClient, invalid_range: str):
    """Test invalid range header formats."""
    uid = f"invalid-range-{hash(invalid_range) % 10000}"  # Unique UID for each test
    file_content, file_metadata = generate_test_file(size_in_kb=4)

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        async def test_invalid_ranges():
            await anyio.sleep(0.5)
            headers = {'Range': invalid_range}
            response = await test_client.get(f"/{uid}?download=true", headers=headers)

            # Should return full file (200) when range is invalid
            assert response.status_code == 200, \
                f"Invalid range '{invalid_range}' should return full file (200), got {response.status_code}"
            assert len(response.content) == file_metadata.size, \
                f"Should get full file for invalid range, got {len(response.content)} bytes"

        async with anyio.create_task_group() as tg:
            tg.start_soon(test_invalid_ranges)
            await ws.upload_with_metadata(file_content, file_metadata)


@pytest.mark.anyio
async def test_range_download_resumption(test_client: HTTPTestClient, websocket_client: WebSocketTestClient):
    """Test using range requests to resume interrupted downloads."""
    uid = "range-resume"
    file_content, file_metadata = generate_test_file(size_in_kb=16)

    async with websocket_client.websocket_connect(f"/send/{uid}") as ws:
        await ws.send_file_metadata(file_metadata)

        downloaded_parts = []

        async def download_in_parts():
            await anyio.sleep(0.5)

            # Simulate downloading file in 4KB chunks using range requests
            chunk_size = 4096
            for offset in range(0, file_metadata.size, chunk_size):
                end = min(offset + chunk_size - 1, file_metadata.size - 1)
                response = await test_client.download_with_range(uid, offset, end)

                downloaded_parts.append(response.content)

                # Simulate interruption and resumption
                if offset == 8192:
                    await anyio.sleep(0.5)  # Pause mid-download

            # Verify reassembled file
            reassembled = b''.join(downloaded_parts)
            assert len(reassembled) == file_metadata.size, f"Reassembled file should be {file_metadata.size} bytes, got {len(reassembled)}"
            assert reassembled == file_content, "Reassembled content should match original"

        async with anyio.create_task_group() as tg:
            tg.start_soon(download_in_parts)

            await ws.wait_for_go_signal()
            await ws.upload_file_chunks(file_content, delay=0.01)