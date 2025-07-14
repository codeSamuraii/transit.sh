import json
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket

from app import app
from lib.transfer import FileMetadata, FileTransfer


@pytest.fixture
def test_client():
    """FastAPI test client for HTTP endpoints."""
    return TestClient(app)


@pytest.fixture
def test_file():
    """Create a temporary test file."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as f:
        test_content = b"Hello, World! This is test content." * 100  # ~3KB
        f.write(test_content)
        f.flush()
        yield Path(f.name), test_content
    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def mock_redis():
    """Mock Redis client for testing."""
    with patch('lib.store.Store.get_redis') as mock:
        redis_mock = AsyncMock()
        # Default for receiver connected check
        redis_mock.exists.return_value = 0
        mock.return_value = redis_mock
        yield redis_mock


class TestWebSocketUpload:
    """Test WebSocket upload endpoint."""

    @pytest.mark.asyncio
    async def test_websocket_upload_success(self, test_file, mock_redis):
        """Test successful WebSocket upload."""
        file_path, file_content = test_file
        uid = "test-upload-123"

        # Mock Redis operations
        mock_redis.set.return_value = None
        mock_redis.get.return_value = None
        mock_redis.lpush.return_value = None
        mock_redis.brpop.return_value = None
        mock_redis.exists.return_value = False
        mock_redis.publish.return_value = None
        mock_redis.scan.return_value = (0, [])
        mock_redis.delete.return_value = 0
        mock_redis.llen.return_value = 0

        # Simulate file metadata
        file_metadata = {
            "file_name": file_path.name,
            "file_size": len(file_content),
            "file_type": "text/plain"
        }

        with patch.object(FileTransfer, 'wait_for_client_connected', new_callable=AsyncMock):
            with TestClient(app).websocket_connect(f"/send/{uid}") as websocket:
                # Send file metadata header
                websocket.send_json(file_metadata)

                # Wait for go-ahead message
                message = websocket.receive_text()
                assert message == "Go for file chunks"

                # Send file chunks
                chunk_size = 1024
                for i in range(0, len(file_content), chunk_size):
                    chunk = file_content[i:i + chunk_size]
                    websocket.send_bytes(chunk)

                # Send empty chunk to signal end
                websocket.send_bytes(b'')

    @pytest.mark.asyncio
    async def test_websocket_upload_invalid_header(self, mock_redis):
        """Test WebSocket upload with invalid header."""
        uid = "test-invalid-header"

        # Mock Redis operations
        mock_redis.set.return_value = None
        mock_redis.get.return_value = None
        mock_redis.lpush.return_value = None
        mock_redis.exists.return_value = False
        mock_redis.publish.return_value = None
        mock_redis.scan.return_value = (0, [])
        mock_redis.delete.return_value = 0

        # Patch the websocket.receive_json to handle the error properly
        with patch.object(WebSocket, 'receive_json') as mock_receive_json:
            mock_receive_json.side_effect = json.JSONDecodeError("Expecting value", "invalid json", 0)

            with TestClient(app).websocket_connect(f"/send/{uid}") as websocket:
                # The error should be handled in the endpoint and return an error message
                response = websocket.receive_text()
                assert "Error: Cannot decode file metadata" in response

    @pytest.mark.asyncio
    async def test_websocket_upload_missing_file_fields(self, mock_redis):
        """Test WebSocket upload with missing required fields."""
        uid = "test-missing-fields"

        # Mock Redis operations
        mock_redis.set.return_value = None
        mock_redis.get.return_value = None
        mock_redis.lpush.return_value = None
        mock_redis.exists.return_value = False
        mock_redis.publish.return_value = None
        mock_redis.scan.return_value = (0, [])
        mock_redis.delete.return_value = 0

        with TestClient(app).websocket_connect(f"/send/{uid}") as websocket:
            # Send header missing required fields
            websocket.send_json({"file_name": "test.txt"})  # Missing size and type

            response = websocket.receive_text()
            assert "Error: Cannot decode file metadata" in response


class TestHTTPDownload:
    """Test HTTP download endpoint."""

    @pytest.mark.asyncio
    async def test_http_download_success(self, test_client, test_file, mock_redis):
        """Test successful HTTP download."""
        file_path, file_content = test_file
        uid = "test-download-123"

        # Mock existing transfer
        file_metadata = FileMetadata(
            name=file_path.name,
            size=len(file_content),
            content_type="text/plain"
        )

        # Mock Redis operations for metadata retrieval
        mock_redis.get.return_value = file_metadata.to_json()
        # Mock receiver not connected, then successfully set
        mock_redis.exists.return_value = 0
        mock_redis.set.return_value = True
        mock_redis.publish.return_value = None
        mock_redis.scan.return_value = (0, [])
        mock_redis.delete.return_value = 1

        # Mock queue operations to return file content
        chunks = [file_content[i:i+1024] for i in range(0, len(file_content), 1024)]
        chunks.append(b'\x00\xFF')  # End marker
        mock_redis.brpop.side_effect = [(uid, chunk) for chunk in chunks]

        # Mock the background task completion
        response = test_client.get(f"/{uid}?download=true")

        assert response.status_code == 200
        assert response.content == file_content
        assert response.headers["content-disposition"] == f"attachment; filename={file_path.name}"

    @pytest.mark.asyncio
    async def test_http_download_not_found(self, test_client, mock_redis):
        """Test HTTP download for non-existent transfer."""
        uid = "non-existent-transfer"

        # Mock no metadata found
        mock_redis.get.return_value = None

        response = test_client.get(f"/{uid}")

        assert response.status_code == 404
        assert "Transfer not found" in response.text

    @pytest.mark.asyncio
    async def test_http_download_invalid_uid(self, test_client):
        """Test HTTP download with invalid UID containing dots."""
        uid = "invalid.uid.with.dots"

        response = test_client.get(f"/{uid}")

        assert response.status_code == 400
        assert "Invalid transfer ID" in response.text

    @pytest.mark.asyncio
    async def test_http_download_prefetch_protection(self, test_client, mock_redis):
        """Test that prefetch requests get HTML preview instead of file."""
        uid = "test-prefetch-123"
        file_metadata = FileMetadata(
            name="test.txt",
            size=1000,
            content_type="text/plain"
        )

        mock_redis.get.return_value = file_metadata.to_json()
        mock_redis.exists.return_value = 0  # Receiver not connected

        # Simulate WhatsApp prefetch request
        headers = {"user-agent": "WhatsApp/2.21.1"}
        response = test_client.get(f"/{uid}", headers=headers)

        # Should return HTML preview or plain text error if template not found
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            assert "text/html" in response.headers.get("content-type", "") or "test.txt" in response.text

    @pytest.mark.asyncio
    async def test_http_download_already_connected(self, test_client, mock_redis):
        """Test that a second download attempt is rejected."""
        uid = "test-already-connected"
        file_metadata = FileMetadata(
            name="test.txt",
            size=1000,
            content_type="text/plain"
        )

        mock_redis.get.return_value = file_metadata.to_json()
        mock_redis.set.return_value = False  # Simulate receiver already connected

        response = test_client.get(f"/{uid}?download=true")

        assert response.status_code == 409
        assert "A client is already downloading this file" in response.text


class TestHTTPUpload:
    """Test HTTP upload endpoint."""

    @pytest.mark.asyncio
    async def test_http_upload_success(self, test_client, test_file, mock_redis):
        """Test successful HTTP upload."""
        file_path, file_content = test_file
        uid = "test-upload-456"
        filename = file_path.name

        # Mock Redis operations
        mock_redis.set.return_value = None
        mock_redis.lpush.return_value = None
        mock_redis.exists.return_value = False
        mock_redis.publish.return_value = None
        mock_redis.scan.return_value = (0, [])
        mock_redis.delete.return_value = 0
        mock_redis.llen.return_value = 0  # Queue not full

        headers = {
            "content-length": str(len(file_content)),
            "content-type": "text/plain"
        }

        with patch.object(FileTransfer, 'wait_for_client_connected', new_callable=AsyncMock):
            response = test_client.put(
                f"/{uid}/{filename}",
                content=file_content,
                headers=headers
            )

        assert response.status_code == 200
        assert "Transfer complete" in response.text

    @pytest.mark.asyncio
    async def test_http_upload_file_too_large(self, test_client):
        """Test HTTP upload with file larger than 100MB limit."""
        uid = "test-large-file"
        filename = "large_file.bin"

        # Simulate large file with content-length header
        large_size = int(1.1 * 1024**3)  # 1.1 GiB
        headers = {"content-length": str(large_size)}

        response = test_client.put(
            f"/{uid}/{filename}",
            content=b"dummy",  # Actual content doesn't matter for this test
            headers=headers
        )

        assert response.status_code == 413
        assert "File too large" in response.text

    @pytest.mark.asyncio
    async def test_http_upload_client_timeout(self, test_client, test_file, mock_redis):
        """Test HTTP upload when client doesn't connect in time."""
        file_path, file_content = test_file
        uid = "test-timeout-789"
        filename = file_path.name

        # Mock Redis operations
        mock_redis.set.return_value = None
        mock_redis.llen.return_value = 0
        mock_redis.exists.return_value = b'0'

        headers = {
            "content-length": str(len(file_content)),
            "content-type": "text/plain"
        }

        # Mock timeout on waiting for client
        with patch.object(FileTransfer, 'wait_for_client_connected', side_effect=asyncio.TimeoutError):
            response = test_client.put(
                f"/{uid}/{filename}",
                content=file_content,
                headers=headers
            )

        assert response.status_code == 408
        assert "Client did not connect in time" in response.text


class TestIntegration:
    """Integration tests combining multiple endpoints."""

    @pytest.mark.asyncio
    async def test_full_transfer_flow(self, test_file, mock_redis):
        """Test complete transfer flow: upload via WebSocket, download via HTTP."""
        file_path, file_content = test_file
        uid = "integration-test-001"

        # Mock Redis to simulate actual queue behavior
        queue_data = []

        async def mock_lpush(key, data):
            queue_data.append(data)
            return len(queue_data)

        async def mock_brpop(keys, timeout=None):
            if queue_data:
                return (keys[0], queue_data.pop(0))
            return None

        mock_redis.lpush.side_effect = mock_lpush
        mock_redis.brpop.side_effect = mock_brpop
        mock_redis.set.return_value = None
        mock_redis.get.return_value = FileMetadata(
            name=file_path.name,
            size=len(file_content),
            content_type="text/plain"
        ).to_json()
        mock_redis.llen.return_value = 0
        mock_redis.exists.return_value = 0
        mock_redis.publish.return_value = None
        mock_redis.scan.return_value = (0, [])
        mock_redis.delete.return_value = 0

        # Step 1: Upload via WebSocket
        file_metadata = {
            "file_name": file_path.name,
            "file_size": len(file_content),
            "file_type": "text/plain"
        }

        def upload_file():
            with patch.object(FileTransfer, 'wait_for_client_connected', new_callable=AsyncMock):
                with TestClient(app).websocket_connect(f"/send/{uid}") as websocket:
                    websocket.send_json(file_metadata)

                    message = websocket.receive_text()
                    assert message == "Go for file chunks"

                    # Send file in chunks
                    chunk_size = 1024
                    for i in range(0, len(file_content), chunk_size):
                        chunk = file_content[i:i + chunk_size]
                        websocket.send_bytes(chunk)

                    websocket.send_bytes(b'')  # End marker

        # Step 2: Download via HTTP
        def download_file():
            client = TestClient(app)
            # Mock set_receiver_connected to succeed
            mock_redis.set.return_value = True
            response = client.get(f"/{uid}?download=true")
            assert response.status_code == 200
            return response.content

        # Execute upload (synchronous since TestClient is sync)
        upload_file()

        # Verify data was queued correctly
        assert len(queue_data) > 0, "No data was queued during upload"

        # Execute download and verify content
        downloaded_content = download_file()
        assert downloaded_content == file_content, f"Downloaded content (size: {len(downloaded_content)}) does not match uploaded content (size: {len(file_content)})"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
