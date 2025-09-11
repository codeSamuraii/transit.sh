import anyio
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
        """Create a new transfer using the provided identifier and file metadata."""
        transfer = cls(uid, file)
        await transfer.store.set_metadata(file.to_json())
        return transfer

    @classmethod
    async def get(cls, uid: str):
        """Fetch a transfer from the store using the provided identifier."""
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

    @property
    async def receiver_connected(self) -> bool:
        """Check if a receiver is actively downloading."""
        return await self.store.is_receiver_active()

    async def notify_receiver_connected(self):
        """Notify sender that receiver connected."""
        await self.store.set_event('receiver_connected')

    async def wait_for_receiver(self):
        """Wait for receiver to connect."""
        self.info(f"△ Waiting for receiver...")
        await self.store.wait_for_event('receiver_connected')
        self.debug(f"△ Receiver connected")

    async def consume_upload(self, stream: AsyncIterator[bytes], on_error: Callable[[Exception | str], Awaitable[None]]) -> None:
        """Consume upload stream and add chunks to Redis stream."""
        self.bytes_uploaded = 0

        try:
            async for chunk in stream:
                if not chunk:
                    break

                await self.store.add_chunk(chunk)
                self.bytes_uploaded += len(chunk)

            if self.bytes_uploaded < self.file.size:
                raise TransferError("Incomplete upload", propagate=True)

            await self.store.add_chunk(self.DONE_FLAG)
            self.debug(f"△ All data chunks uploaded: {self.bytes_uploaded} bytes")

        except (ClientDisconnect, WebSocketDisconnect):
            self.error(f"△ Sender disconnected")
            await self.store.add_chunk(self.DEAD_FLAG)

        except TimeoutError:
            self.warning(f"△ Upload timeout")
            await on_error("Upload timeout")

        except TransferError as e:
            if e.propagate:
                await self.store.add_chunk(self.DEAD_FLAG)
            await on_error(e)

    async def produce_download(self, on_error: Callable[[Exception | str], Awaitable[None]]) -> AsyncIterator[bytes]:
        """Produce download stream from Redis stream."""
        self.bytes_downloaded = await self.store.get_progress()

        if self.bytes_downloaded > 0:
            self.info(f"▼ Resuming from byte {self.bytes_downloaded}")

        try:
            await self.store.set_receiver_active()

            async for chunk in self.store.stream_chunks():
                if chunk == self.DEAD_FLAG:
                    raise TransferError("Sender disconnected")

                if chunk == self.DONE_FLAG:
                    if self.bytes_downloaded >= self.file.size:
                        self.debug(f"▼ All data chunks downloaded: {self.bytes_downloaded} bytes")
                    break

                self.bytes_downloaded += len(chunk)
                await self.store.save_progress(self.bytes_downloaded)
                await self.store.set_receiver_active()
                yield chunk

        except TransferError as e:
            await on_error(e)
        except Exception as e:
            self.error(f"▼ Download error", exc_info=True)
            await on_error(e)

    async def cleanup(self):
        """Clean up transfer data."""
        await self.store.cleanup()

    async def finalize_download(self):
        """Finalize download and cleanup if complete."""
        if self.bytes_downloaded < self.file.size:
            self.info(f"▼ Download paused at {self.bytes_downloaded}/{self.file.size} bytes")
        else:
            await self.cleanup()
