import json
import asyncio
from dataclasses import dataclass, asdict
from starlette.datastructures import Headers
from starlette.responses import ClientDisconnect
from typing import AsyncIterator, Literal, Optional, Self
from fastapi import HTTPException, WebSocketException, status

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
        log = get_logger(self.uid)
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
    def get_exception(detail: str, protocol: Literal['http', 'ws'] = 'http'):
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

    async def set_client_connected(self):
        self.debug(f"▼ Notifying sender that receiver is connected...")
        await self.store.set_event('client_connected')

    async def wait_for_client_connected(self):
        self.info(f"△ Waiting for client to connect...")
        await self.wait_for_event('client_connected')
        self.debug(f"△ Received client connected notification.")

    async def collect_upload(self, stream: AsyncIterator[bytes], protocol: Literal['http', 'ws'] = 'ws'):
        bytes_processed = 0

        try:
            async for chunk in stream:
                if not chunk and bytes_processed < self.file.size:
                    raise ClientDisconnect("Received less data than expected.")
                elif not chunk:
                    self.debug(f"△ All chunks uploaded.")
                    await self.store.put_in_queue(chunk)
                    break

                await self.store.put_in_queue(chunk)
                bytes_processed += len(chunk)

        except ClientDisconnect:
            self.warning(f"△ Upload interrupted, notifying receiver...")
            await self.store.put_in_queue(b'\xde\xad\xbe\xef')  # Special end marker
        except asyncio.TimeoutError:
            self.warning(f"△ Timeout during upload.")

    async def supply_download(self, protocol: Literal['http', 'ws'] = 'http'):
        bytes_processed = 0

        try:
            while True:
                chunk = await self.store.get_from_queue()

                if chunk == b'\xde\xad\xbe\xef':
                    raise ClientDisconnect("Sender disconnected.")
                elif not chunk and bytes_processed < self.file.size:
                    raise ClientDisconnect("Received less data than expected.")
                elif not chunk:
                    self.debug(f"▼ All chunks received.")
                    break

                bytes_processed += len(chunk)
                yield chunk

        except ClientDisconnect as e:
            self.warning(f"▼ {str(e)}")
        except asyncio.TimeoutError:
            self.warning(f"▼ Timeout during download.")

    async def cleanup(self):
        try:
            num_keys = await asyncio.wait_for(self.store.cleanup(), timeout=30.0)
        except asyncio.TimeoutError:
            self.warning(f"- Cleanup timed out.")
            pass

        if num_keys:
            self.info(f"- Cleanup: {num_keys} keys removed.")
