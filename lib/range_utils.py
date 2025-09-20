from typing import Optional, Tuple, List
from dataclasses import dataclass


@dataclass
class RangeRequest:
    """Represents a parsed HTTP Range request."""
    start: int
    end: Optional[int]
    total_size: Optional[int] = None

    @property
    def length(self) -> Optional[int]:
        """Calculate the length of the requested range."""
        if self.end is not None:
            return self.end - self.start + 1
        elif self.total_size is not None:
            return self.total_size - self.start
        return None

    def to_content_range(self, total_size: int) -> str:
        """Generate Content-Range header value."""
        end = self.end if self.end is not None else total_size - 1
        return f"bytes {self.start}-{end}/{total_size}"


class RangeParser:
    """Parses and validates HTTP Range headers."""

    @staticmethod
    def parse_range_header(range_header: Optional[str], file_size: int) -> Optional[RangeRequest]:
        """Parse Range header and return RangeRequest object."""
        if not range_header or not range_header.startswith('bytes='):
            return None

        try:
            range_spec = range_header[6:]  # Remove 'bytes='

            if ',' in range_spec:
                # Multiple ranges not supported for now
                return None

            if '-' not in range_spec:
                return None

            parts = range_spec.split('-', 1)
            start_str, end_str = parts[0], parts[1]

            # Handle suffix-length syntax (e.g., "-500" for last 500 bytes)
            if not start_str and end_str:
                suffix_length = int(end_str)
                start = max(0, file_size - suffix_length)
                end = file_size - 1
                return RangeRequest(start=start, end=end, total_size=file_size)

            # Handle normal range
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1

            # Validate range
            if start < 0 or start >= file_size:
                return None
            if end >= file_size:
                end = file_size - 1
            if start > end:
                return None

            return RangeRequest(start=start, end=end, total_size=file_size)

        except (ValueError, IndexError):
            return None

    @staticmethod
    def validate_if_range(if_range_header: Optional[str], etag: Optional[str]) -> bool:
        """Validate If-Range header against ETag."""
        if not if_range_header or not etag:
            return True  # No validation needed
        return if_range_header == etag

    @staticmethod
    def calculate_chunk_range(chunk_index: int, chunk_size: int, byte_offset: int) -> Tuple[int, int]:
        """Calculate byte range for a specific chunk."""
        chunk_start = chunk_index * chunk_size
        chunk_end = chunk_start + chunk_size - 1

        if chunk_start < byte_offset:
            # Partial chunk at the beginning
            return byte_offset, chunk_end
        return chunk_start, chunk_end

    @staticmethod
    def is_partial_content(range_request: Optional[RangeRequest]) -> bool:
        """Check if this is a partial content request."""
        return range_request is not None and (
            range_request.start > 0 or
            (range_request.end is not None and range_request.total_size is not None and
             range_request.end < range_request.total_size - 1)
        )

    @staticmethod
    def create_content_headers(range_request: RangeRequest, file_type: str) -> dict:
        """Create response headers for partial content."""
        headers = {
            'Content-Type': file_type,
            'Accept-Ranges': 'bytes',
            'Content-Range': range_request.to_content_range(range_request.total_size)
        }

        if range_request.length:
            headers['Content-Length'] = str(range_request.length)

        return headers