from starlette.datastructures import Headers
from pydantic import BaseModel, ByteSize, ConfigDict, Field, field_validator, StrictStr
from typing import Annotated, Optional, Self
import re


class FileMetadata(BaseModel):
    name: StrictStr = Field(description="File name", min_length=1, max_length=255)
    size: ByteSize = Field(description="Size in bytes", gt=0)
    type: StrictStr = Field(description="MIME type", default='application/octet-stream')

    model_config = ConfigDict(title="File transfer metadata", alias_generator=lambda s: f'file_{s}', populate_by_name=True, validate_by_name=True)

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Filename cannot be empty")

        safe_filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', str(v)).strip()
        if not safe_filename:
            raise ValueError("Filename contains only invalid characters")

        try:
            safe_filename = safe_filename.encode('utf-8').decode('utf-8')
        except UnicodeError:
            safe_filename = safe_filename.encode('utf-8', 'ignore').decode('utf-8', 'ignore')

        return safe_filename

    @classmethod
    def from_json(cls, data: str) -> Self:
        return cls.model_validate_json(data)

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def get_from_http_headers(cls, headers: Headers, filename: str) -> Self:
        """Create metadata from headers of an HTTP upload request."""
        return cls(
            name=filename,
            size=headers.get('content-length', '0'),
            type=headers.get('content-type', '')  # Must be a string
        )

    @classmethod
    def get_from_json(cls, header: dict) -> Self:
        """Create metadata from a JSON dictionary."""
        return cls(**header)

    def to_readable_dict(self) -> dict:
        return dict(
            file_name=self.name,
            file_size=self.size.human_readable(),
            file_type=self.type,
        )

    def __str__(self):
        return f"{self.name} ({self.size.human_readable()} - {self.type})"

    def __repr__(self):
        return f"FileMetadata(name={self.name!r}, size={self.size.human_readable()}, type={self.type!r})"
