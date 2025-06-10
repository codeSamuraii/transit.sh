import json
import asyncio
from dataclasses import dataclass, asdict
from typing import AsyncIterator, Optional, Dict, Any
from fastapi import HTTPException, Request, WebSocketException

from .store import Store


@dataclass(frozen=True)
class File:
    size: int
    name: Optional[str] = None
    content_type: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> 'File':
        return cls(**json.loads(data))

    @classmethod
    def get_file_from_request(cls, request: Request) -> 'File':
        return cls(
            name=request.path_params.get('filename'),
            size=int(request.headers.get('content-length') or 1),
            content_type=request.headers.get('content-type')
        )

    @classmethod
    def get_file_from_header(cls, header: dict) -> 'File':
        return cls(
            name=header['file_name'].encode('ascii', 'replace').decode(),
            size=int(header['file_size']),
            content_type=header['file_type']
        )


class FileTransfer:

    def __init__(self, uid: str, file: File):
        self.uid = uid
        self.file = file
        self.store = Store(uid)

    @classmethod
    async def create(cls, uid: str, file: File):
        transfer = cls(uid, file)
        await transfer.store.set_metadata(file.to_json())
        return transfer

    @classmethod
    async def get(cls, uid: str):
        store = Store(uid)
        metadata_json = await store.get_metadata()
        if not metadata_json:
            raise KeyError(f"FileTransfer '{uid}' not found.")

        file = File.from_json(metadata_json)
        return cls(uid, file)

    def get_file_info(self):
        return self.file.name, self.file.size, self.file.content_type

    async def set_event(self, event_name: str):
        await self.store.set_event(event_name)

    async def wait_for_event(self, event_name: str, timeout: float = 300.0):
        await self.store.wait_for_event(event_name, timeout)

    async def collect_upload(self, stream: AsyncIterator[bytes]):
        try:
            while True:
                chunk = await anext(stream, None)
                if not chunk:
                    print(f"{self.uid} △ Received empty chunk, ending upload...")
                    break
                await self.store.put_in_queue(chunk)

            await self.store.put_in_queue(b'')
            await self.wait_for_event('transfer_complete', timeout=3600)
            print(f"{self.uid} △ Received transfer complete signal.")

        except asyncio.TimeoutError as e:
            print(f"{self.uid} △ Timeout receiving data: {e}")
            raise WebSocketException(4000, "Transfer timed out, other peer likely disconnected.")
        finally:
            await self.store.cleanup()

    async def supply_download(self):
        try:
            while True:
                chunk = await self.store.get_from_queue()
                if not chunk:
                    print(f"{self.uid} ▼ No more chunks to receive.")
                    break
                yield chunk

            await self.set_event("transfer_complete")
            print(f"{self.uid} ▼ Transfer complete, notified all waiting tasks.")
        except asyncio.TimeoutError as e:
            print(f"{self.uid} △ Timeout receiving data: {e}")
            raise HTTPException(400, "Transfer timed out, other peer likely disconnected.")
        finally:
            await self.store.cleanup()

