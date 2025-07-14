import json
import math
from dataclasses import dataclass, asdict
from starlette.datastructures import Headers
from typing import Optional, Self


@dataclass(frozen=True)
class FileMetadata:
    size: int
    name: str
    content_type: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), skipkeys=True)

    def to_readable_dict(self) -> dict:
        return dict(
            file_name=self.name,
            file_size=self.format_size(self.size),
            file_type=self.format_type(self.content_type)
        )

    @classmethod
    def from_json(cls, data: str) -> Self:
        return cls(**json.loads(data))

    @classmethod
    def get_from_http_headers(cls, headers: Headers, filename: str) -> Self:
        return cls(
            name=cls.escape_filename(filename),
            size=cls.process_length(headers.get('content-length', '0')),
            content_type=headers.get('content-type', '')
        )

    @classmethod
    def get_from_json(cls, header: dict) -> Self:
        return cls(
            name=cls.escape_filename(header['file_name']),
            size=cls.process_length(header['file_size']),
            content_type=header['file_type']
        )

    @staticmethod
    def escape_filename(filename: str) -> str:
        """Escape special characters in the filename."""
        return str(filename).encode('latin-1', 'ignore').decode('utf-8', 'ignore')

    @staticmethod
    def format_size(size_bytes: int) -> str:
        """Return human-readable file size."""
        if size_bytes == 0:
            return "0 B"
        units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB")
        i = math.floor(math.log(size_bytes, 1024))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 1)
        return f"{s} {units[i]}"

    @staticmethod
    def format_type(content_type: Optional[str]) -> str:
        """Return human-readable file type."""
        return content_type or "unknown"

    @staticmethod
    def process_length(length: str | int) -> int:
        """Convert size string to bytes."""
        try:
            size = int(str(length).strip().replace(' ', ''))
        except ValueError:
            raise ValueError(f"Invalid size format: {length}")
        if size <= 0:
            raise ValueError("File size has to be positive.")
        return size

    def __str__(self):
        return f"{self.name} ({self.size/(1024**2):.1f} MiB - {self.content_type})"

    def __repr__(self):
        return f"FileMetadata(name={self.name!r}, size={self.size/(1024**2):.1f}, content_type={self.content_type!r})"
