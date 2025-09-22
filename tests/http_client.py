import httpx
from typing import AsyncIterator
import anyio

from lib.metadata import FileMetadata


class HTTPTestClient(httpx.AsyncClient):
    """Enhanced HTTP test client with helper methods for common testing operations."""

    async def download_with_range(self, uid: str, start: int, end: int, expected_status: int = 206) -> httpx.Response:
        """Download file with Range header and verify status."""
        headers = {'Range': f'bytes={start}-{end}'}
        response = await self.get(f"/{uid}?download=true", headers=headers)
        assert response.status_code == expected_status, \
            f"Range {start}-{end} should return {expected_status}, got {response.status_code}"
        return response

    async def download_full_file(self, uid: str, expected_status: int = 200) -> bytes:
        """Download complete file and return content."""
        response = await self.get(f"/{uid}?download=true")
        assert response.status_code == expected_status, \
            f"Download should return {expected_status}, got {response.status_code}"

        downloaded = b''
        async with self.stream("GET", f"/{uid}?download=true") as stream_response:
            async for chunk in stream_response.aiter_bytes(4096):
                downloaded += chunk

        return downloaded

    async def download_in_ranges(self, uid: str, file_size: int, chunk_size: int = 4096) -> bytes:
        """Download file using multiple range requests and return reassembled content."""
        downloaded_parts = []

        for offset in range(0, file_size, chunk_size):
            end = min(offset + chunk_size - 1, file_size - 1)
            response = await self.download_with_range(uid, offset, end)
            downloaded_parts.append(response.content)

        return b''.join(downloaded_parts)

    def verify_range_response(self, response: httpx.Response, start: int, end: int, expected_content: bytes) -> None:
        """Verify range response has correct status, length, content, and headers."""
        expected_length = end - start + 1
        assert len(response.content) == expected_length, \
            f"Range {start}-{end} should return {expected_length} bytes, got {len(response.content)}"
        assert response.content == expected_content, \
            f"Range {start}-{end} content doesn't match expected data"

        # Verify Content-Range header
        content_range = response.headers.get('content-range')
        assert content_range and content_range.startswith(f"bytes {start}-{end}/"), \
            f"Content-Range should start with 'bytes {start}-{end}/', got '{content_range}'"

    async def verify_file_integrity(self, downloaded_content: bytes, original_content: bytes, file_size: int):
        """Verify downloaded file matches original."""
        assert len(downloaded_content) == file_size, \
            f"Downloaded size should be {file_size}, got {len(downloaded_content)}"
        assert downloaded_content == original_content, \
            "Downloaded content should match original file"

    async def upload_file_http(self, uid: str, file_content: bytes, file_metadata: FileMetadata) -> httpx.Response:
        """Upload file via HTTP PUT and return response."""
        headers = {
            'Content-Type': file_metadata.type,
            'Content-Length': str(file_metadata.size)
        }
        response = await self.put(f"/{uid}/{file_metadata.name}", content=file_content, headers=headers)
        return response