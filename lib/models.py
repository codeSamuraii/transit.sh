import enum
from pydantic import BaseModel, Field
from typing import Optional


class UploadProgress(BaseModel):
    bytes_uploaded: int = Field(default=0, ge=0)
    last_chunk_id: str = Field(default='0')

    def to_redis(self) -> dict:
        return {
            'bytes_uploaded': self.bytes_uploaded,
            'last_chunk_id': self.last_chunk_id
        }

    @classmethod
    def from_redis(cls, data: dict) -> Optional['UploadProgress']:
        if not data:
            return None
        return cls(
            bytes_uploaded=int(data.get(b'bytes_uploaded', 0)),
            last_chunk_id=data.get(b'last_chunk_id', b'0').decode()
        )


class DownloadProgress(BaseModel):
    bytes_downloaded: int = Field(default=0, ge=0)
    last_read_id: str = Field(default='0')

    def to_redis(self) -> dict:
        return {
            'bytes_downloaded': self.bytes_downloaded,
            'last_read_id': self.last_read_id
        }

    @classmethod
    def from_redis(cls, data: dict) -> Optional['DownloadProgress']:
        if not data:
            return None
        return cls(
            bytes_downloaded=int(data.get(b'bytes_downloaded', 0)),
            last_read_id=data.get(b'last_read_id', b'0').decode()
        )


class ResumeInfo(BaseModel):
    resume_from: int = Field(default=0, ge=0)
    last_chunk_id: str = Field(default='0')
    can_resume: bool = Field(default=False)


class ClientState(enum.IntEnum):
    """Client connection states."""
    ERROR = -1                  # Unrecoverable error occurred
    DISCONNECTED = 0            # Client disconnected, waiting for reconnection
    ACTIVE = 1                  # Actively transferring
    COMPLETE = 2                # Transfer complete