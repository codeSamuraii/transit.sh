import json
import asyncio
from dataclasses import dataclass, asdict
from starlette.datastructures import Headers
from typing import AsyncIterator, Literal, Optional, Self
from fastapi import HTTPException, Request, WebSocketException, status

from .store import Store
from .logging import get_logger


@dataclass(frozen=True)
class FileMetadata:
    size: int
    name: Optional[str] = None
    content_type: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> Self:
        return cls(**json.loads(data))

    @classmethod
    def get_from_http_headers(cls, headers: Headers, filename: str) -> Self:
        return cls(
            name=filename,
            size=int(headers.get('content-length') or '0'),
            content_type=headers.get('content-type')
        )

    @classmethod
    def get_file_from_json(cls, header: dict) -> Self:
        return cls(
            name=header['file_name'].encode('ascii', 'replace').decode(),
            size=int(header['file_size']),
            content_type=header['file_type']
        )


class FileTransfer:

    def __init__(self, uid: str, file: FileMetadata):
        self.uid = self._format_uid(uid)
        self.file = file
        self.store = Store(self.uid)
        log = get_logger(f'{self.uid}')
        self.debug, self.info, self.warning, self.error = log.debug, log.info, log.warning, log.error

    @classmethod
    async def create(cls, uid: str, file: FileMetadata):
        transfer = cls(uid, file)
        await transfer.store.set_metadata(file.to_json())
        return transfer

    @classmethod
    async def get(cls, uid: str):
        store = Store(uid)
        metadata_json = await store.get_metadata()
        if not metadata_json:
            raise KeyError(f"FileTransfer '{uid}' not found.")

        file = FileMetadata.from_json(metadata_json)
        return cls(uid, file)

    @staticmethod
    def timeout_exception(protocol: Literal['http', 'ws'] = 'http'):
        detail = "Transfer timed out, other peer likely disconnected."
        if protocol == 'ws':
            return WebSocketException(status.WS_1006_ABNORMAL_CLOSURE, detail)
        else:
            return HTTPException(status.HTTP_408_REQUEST_TIMEOUT, detail)

    @staticmethod
    def _format_uid(uid: str):
        return str(uid).strip().encode('ascii', 'ignore').decode()

    def get_file_info(self):
        return self.file.name, self.file.size, self.file.content_type

    async def wait_for_event(self, event_name: str, timeout: float = 300.0):
        await self.store.wait_for_event(event_name, timeout)

    async def set_transfer_complete(self):
        self.debug(f"▼ Notifying transfer is complete...")
        await self.store.set_event('transfer_complete')

    async def set_client_connected(self):
        self.debug(f"▼ Notifying client is connected...")
        await self.store.set_event('client_connected')

    async def wait_for_transfer_complete(self):
        self.info(f"△ Waiting for transfer to complete...")
        await self.wait_for_event('transfer_complete')
        self.info(f"△ Received completion confirmation.")

    async def wait_for_client_connected(self):
        self.info(f"△ Waiting for client to connect...")
        await self.wait_for_event('client_connected')
        self.info(f"△ Received client connected notification.")

    async def collect_upload(self, stream: AsyncIterator[bytes], protocol: Literal['http', 'ws'] = 'ws'):
        try:
            async for chunk in stream:
                if not chunk:
                    self.info(f"△ Finishing upload...")
                    break
                await self.store.put_in_queue(chunk)

            await self.store.put_in_queue(b'')
            await self.wait_for_transfer_complete()

        except asyncio.TimeoutError as e:
            self.warning(f"△ Timeout collecting uploading data: {e}")
            raise self.timeout_exception(protocol)

        finally:
            await self.cleanup()

    async def supply_download(self, protocol: Literal['http', 'ws'] = 'http'):
        try:
            while True:
                chunk = await self.store.get_from_queue()
                if not chunk:
                    self.debug(f"▼ No more chunks to receive.")
                    break
                yield chunk

            await self.set_transfer_complete()
            self.info(f"▼ Download complete.")

        except asyncio.TimeoutError as e:
            self.warning(f"▼ Timeout fetching download data: {e}")
            raise self.timeout_exception(protocol)

        finally:
            await self.cleanup()

    async def cleanup(self):
        try:
            num_keys = await asyncio.wait_for(self.store.cleanup(), timeout=30.0)
        except asyncio.TimeoutError:
            self.warning(f"- Cleanup timed out.")
            pass

        self.info(f"- Cleanup complete, removed {num_keys} keys.")
