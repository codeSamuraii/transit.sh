from starlette.datastructures import Headers
from pydantic import BaseModel, Field, field_validator, ByteSize, StrictStr, ConfigDict, AliasChoices
from typing import Optional, Self, Annotated


class FileMetadata(BaseModel):
    name: StrictStr = Field(description="File name", min_length=2, max_length=255, validation_alias=AliasChoices('name', 'file_name'))
    size: ByteSize = Field(description="Size in bytes", gt=0,validation_alias=AliasChoices('size', 'file_size'))
    type: StrictStr = Field(description="MIME type", default='application/octet-stream',validation_alias=AliasChoices('type', 'file_type', 'content_type'))

    model_config = ConfigDict(validate_by_name=True, populate_by_name=True)

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        safe_filename = str(v).translate(str.maketrans(':;|*@/\\', '       ')).strip()
        return safe_filename.encode('latin-1', 'ignore').decode('utf-8', 'ignore')

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
            type=headers.get('content-type', '') or None
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
