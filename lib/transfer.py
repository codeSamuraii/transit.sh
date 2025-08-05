import asyncio
from starlette.responses import ClientDisconnect
from starlette.websockets import WebSocketDisconnect
from typing import AsyncIterator, Callable, Awaitable, Optional

from lib.store import Store
from lib.logging import get_logger
from lib.metadata import FileMetadata


class FileTransferError(Exception):
    """Base class for file transfer errors."""
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        super().__init__(*args)
        for name, value in kwargs.items():
            setattr(self, name, value)

    def __str__(self):
        kwargs_str = ', '.join(f"{k}={v}" for k, v in self.kwargs.items())
        details = f" - {kwargs_str}" if kwargs_str else ''
        return super().__str__() + details

    def __repr__(self):
        return self.__class__.__name__ + f"({', '.join(map(repr, self.args))}, {self.kwargs})"


class FileTransfer:

    DONE_FLAG = b'\x00\xFF'
    DEAD_FLAG = b'\xDE\xAD'

    def __init__(self, uid: str, file: FileMetadata):
        self.uid = self._format_uid(uid)
        self.file = file
        self.store = Store(self.uid)
        self.bytes_uploaded = 0
        self.bytes_downloaded = 0

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
                    raise FileTransferError("Transfer was interrupted by the receiver.", propagate=False, shutdown=True)

                await self.store.put_in_queue(chunk)
                self.bytes_uploaded += len(chunk)

            if self.bytes_uploaded < self.file.size:
                raise FileTransferError("Received less data than expected.", propagate=True, shutdown=False)

            self.debug(f"△ End of upload, sending done marker.")
            await self.store.put_in_queue(self.DONE_FLAG)

        except (ClientDisconnect, WebSocketDisconnect) as e:
            self.error(f"△ Unexpected upload error: {e}")
            await self.store.put_in_queue(self.DEAD_FLAG)

        except asyncio.TimeoutError as e:
            self.warning(f"△ Timeout during upload.")
            await on_error("Timeout during upload.")

        except FileTransferError as e:
            self.warning(f"△ Upload error: {e}")
            if e.propagate:
                await self.store.put_in_queue(self.DEAD_FLAG)
            if e.shutdown:
                await on_error(e)

        finally:
            await asyncio.sleep(1.0)

    async def supply_download(self, on_error: Callable[[Exception | str], Awaitable[None]]) -> AsyncIterator[bytes]:
        self.bytes_downloaded = 0

        try:
            while True:
                chunk = await self.store.get_from_queue()

                if chunk == self.DEAD_FLAG:
                    raise FileTransferError("Sender disconnected.")

                if chunk == self.DONE_FLAG and self.bytes_downloaded < self.file.size:
                    raise FileTransferError("Received less data than expected.")

                elif chunk == self.DONE_FLAG:
                    self.debug(f"▼ Done marker received, ending download.")
                    break

                self.bytes_downloaded += len(chunk)
                yield chunk

        except (ClientDisconnect, WebSocketDisconnect) as e:
            self.error(f"▼ Unexpected download error: {e}")
            return

        except asyncio.TimeoutError as e:
            self.warning(f"▼ Timeout during download.")
            self.debug("Debug info:", exc_info=e, stack_info=True)
            await on_error(e)

        except FileTransferError as e:
            self.warning(f"▼ Download error: {e}")

        finally:
            await asyncio.sleep(1.0)

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

        await asyncio.sleep(2.0)
        await self.cleanup()
