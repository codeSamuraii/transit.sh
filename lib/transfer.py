import json
import asyncio
import logging
from dataclasses import dataclass, asdict
from typing import AsyncIterator, Literal, Optional, LiteralString
from fastapi import HTTPException, Request, WebSocketException, status

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

    @staticmethod
    def timeout_exception(protocol: Literal['http'] | Literal['ws'] = 'http'):
        detail = "Transfer timed out, other peer likely disconnected."
        if protocol == 'ws':
            return WebSocketException(status.WS_1006_ABNORMAL_CLOSURE, detail)
        else:
            return HTTPException(status.HTTP_408_REQUEST_TIMEOUT, detail)

    def get_file_info(self):
        return self.file.name, self.file.size, self.file.content_type

    async def wait_for_event(self, event_name: str, timeout: float = 300.0):
        await self.store.wait_for_event(event_name, timeout)

    async def set_transfer_complete(self):
        print(f"{self.uid} ▼ Notifying transfer is complete...")
        await self.store.set_event('transfer_complete')

    async def set_client_connected(self):
        print(f"{self.uid} ▼ Notifying client is connected...")
        await self.store.set_event('client_connected')

    async def wait_for_transfer_complete(self):
        print(f"{self.uid} △ Waiting for transfer to complete...")
        await self.wait_for_event('transfer_complete')
        print(f"{self.uid} △ Received completion confirmation.")

    async def wait_for_client_connected(self):
        print(f"{self.uid} △ Waiting for client to connect...")
        await self.wait_for_event('client_connected')
        print(f"{self.uid} △ Received client connected notification.")

    async def collect_upload(self, stream: AsyncIterator[bytes], protocol: str = 'ws'):
        try:
            while True:
                chunk = await anext(stream, None)
                if not chunk:
                    print(f"{self.uid} △ Received empty chunk, ending upload...")
                    break
                await self.store.put_in_queue(chunk)

            await self.store.put_in_queue(b'')
            await self.wait_for_transfer_complete()

        except asyncio.TimeoutError as e:
            print(f"{self.uid} △ Timeout collecting uploading data: {e}")
            raise self.timeout_exception(protocol)

        finally:
            await self.store.cleanup()

    async def supply_download(self, protocol: str = 'http'):
        try:
            while True:
                chunk = await self.store.get_from_queue()
                if not chunk:
                    print(f"{self.uid} ▼ No more chunks to receive.")
                    break
                yield chunk

            await self.set_transfer_complete()
            await asyncio.sleep(2)  # Allow time for completion notification

        except asyncio.TimeoutError as e:
            print(f"{self.uid} ▼ Timeout fetching download data: {e}")
            raise self.timeout_exception(protocol)

        finally:
            await self.cleanup()

    async def cleanup(self):
        try:
            await asyncio.wait_for(self.store.cleanup(), timeout=30.0)
        except asyncio.TimeoutError:
            pass
