import asyncio
from starlette.responses import ClientDisconnect
from starlette.websockets import WebSocketDisconnect
from typing import AsyncIterator, Callable, Awaitable, Optional, Any

from lib.store import Store
from lib.metadata import FileMetadata
from lib.logging import HasLogging, get_logger
logger = get_logger('transfer')


class TransferError(Exception):
    """Custom exception for transfer errors with optional propagation control."""
    def __init__(self, *args, propagate: bool = False, **extra: Any) -> None:
        super().__init__(*args)
        self.propagate = propagate
        self.extra = extra

    @property
    def shutdown(self) -> bool:
        """Indicates if the transfer should be shut down (usually the opposite of `propagate`)."""
        return self.extra.get('shutdown', not self.propagate)


class FileTransfer(metaclass=HasLogging, name_from='uid'):
    """Handles file transfers, including metadata queries and data streaming."""

    DONE_FLAG = b'\x00\xFF'
    DEAD_FLAG = b'\xDE\xAD'

    def __init__(self, uid: str, file: FileMetadata):
        self.uid = self._format_uid(uid)
        self.file = file
        self.store = Store(self.uid)
        self.bytes_uploaded = 0
        self.bytes_downloaded = 0

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
    def _format_uid(uid: str):
        return str(uid).strip().encode('ascii', 'ignore').decode()

    def get_file_info(self):
        return self.file.name, self.file.size, self.file.type

    async def wait_for_event(self, event_name: str, timeout: float = 300.0):
        await self.store.wait_for_event(event_name, timeout)

    async def set_client_connected(self):
        self.debug(f"▼ Notifying sender that receiver is connected...")
        await self.store.set_event('client_connected')

    async def wait_for_client_connected(self):
        self.info(f"△ Waiting for client to connect...")
        await self.wait_for_event('client_connected')
        self.debug(f"△ Received client connected notification.")

    async def is_receiver_connected(self) -> bool:
        return await self.store.is_receiver_connected()

    async def set_receiver_connected(self) -> bool:
        return await self.store.set_receiver_connected()

    async def is_interrupted(self) -> bool:
        return await self.store.is_interrupted()

    async def set_interrupted(self):
        await self.store.set_interrupted()

    async def is_completed(self) -> bool:
        return await self.store.is_completed()

    async def set_completed(self):
        await self.store.set_completed()

    async def collect_upload(self, stream: AsyncIterator[bytes], on_error: Callable[[Exception | str], Awaitable[None]]) -> None:
        self.bytes_uploaded = 0

        try:
            async for chunk in stream:
                if not chunk:
                    self.debug(f"△ Empty chunk received, ending upload.")
                    break

                if await self.is_interrupted():
                    raise TransferError("Transfer was interrupted by the receiver.", propagate=False)

                await self.store.put_in_queue(chunk)
                self.bytes_uploaded += len(chunk)

            if self.bytes_uploaded < self.file.size:
                raise TransferError("Received less data than expected.", propagate=True)

            self.debug(f"△ End of upload, sending done marker.")
            await self.store.put_in_queue(self.DONE_FLAG)

        except (ClientDisconnect, WebSocketDisconnect) as e:
            self.error(f"△ Unexpected upload error: {e}")
            await self.store.put_in_queue(self.DEAD_FLAG)

        except asyncio.TimeoutError as e:
            self.warning(f"△ Timeout during upload.")
            await on_error("Timeout during upload.")

        except TransferError as e:
            self.warning(f"△ Upload error: {e}")
            if e.propagate:
                await self.store.put_in_queue(self.DEAD_FLAG)
            else:
                await on_error(e)

        finally:
            await asyncio.sleep(1.0)

    async def supply_download(self, on_error: Callable[[Exception | str], Awaitable[None]]) -> AsyncIterator[bytes]:
        self.bytes_downloaded = 0

        try:
            while True:
                chunk = await self.store.get_from_queue()

                if chunk == self.DEAD_FLAG:
                    raise TransferError("Sender disconnected.")

                if chunk == self.DONE_FLAG and self.bytes_downloaded < self.file.size:
                    raise TransferError("Received less data than expected.")

                elif chunk == self.DONE_FLAG:
                    self.debug(f"▼ Done marker received, ending download.")
                    break

                self.bytes_downloaded += len(chunk)
                yield chunk

        except Exception as e:
            self.error(f"▼ Unexpected download error!", exc_info=True)
            self.debug("Debug info:", stack_info=True)
            await on_error(e)

        except TransferError as e:
            self.warning(f"▼ Download error")
            await on_error(e)

    async def cleanup(self):
        try:
            await asyncio.wait_for(self.store.cleanup(), timeout=30.0)
        except asyncio.TimeoutError:
            self.warning(f"- Cleanup timed out.")
            pass

    async def finalize_download(self):
        # self.debug("▼ Finalizing download...")
        if self.bytes_downloaded < self.file.size and not await self.is_interrupted():
            self.warning("▼ Client disconnected before download was complete.")
            await self.set_interrupted()

        await self.cleanup()
        # self.debug("▼ Finalizing download...")
        if self.bytes_downloaded < self.file.size and not await self.is_interrupted():
            self.warning("▼ Client disconnected before download was complete.")
            await self.set_interrupted()

        await self.cleanup()
