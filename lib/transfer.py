import anyio
from starlette.responses import ClientDisconnect
from starlette.websockets import WebSocketDisconnect
from typing import AsyncIterator, Callable, Awaitable, Optional, Any, Tuple

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

    async def collect_upload(self, stream: AsyncIterator[bytes], on_error: Callable[[Exception | str], Awaitable[None]], resume_from: int = 0) -> None:
        """Collect upload with resume support."""
        self.bytes_uploaded = resume_from
        last_chunk_id = '0'

        if resume_from > 0:
            self.info(f"△ Resuming upload from byte {resume_from}")
            progress = await self.store.get_upload_progress()
            if progress:
                last_chunk_id = progress['last_chunk_id']

        try:
            await self.store.set_peer_state('sender', 'uploading')

            async for chunk in stream:
                if not chunk:
                    self.debug(f"△ Empty chunk received, ending upload.")
                    break

                if await self.is_interrupted():
                    await self.store.save_upload_progress(self.bytes_uploaded, last_chunk_id)
                    await self.store.set_peer_state('sender', 'paused')
                    raise TransferError("Transfer was interrupted by the receiver.", propagate=False)

                last_chunk_id = await self.store.put_chunk(chunk)
                self.bytes_uploaded += len(chunk)

                if self.bytes_uploaded % (64 * 1024) == 0:  # Save progress every 64KB
                    await self.store.save_upload_progress(self.bytes_uploaded, last_chunk_id)

            if self.bytes_uploaded < self.file.size:
                await self.store.save_upload_progress(self.bytes_uploaded, last_chunk_id)
                await self.store.set_peer_state('sender', 'incomplete')
                raise TransferError("Received less data than expected.", propagate=True)

            self.debug(f"△ End of upload, sending done marker.")
            await self.store.put_chunk(self.DONE_FLAG)
            await self.store.set_peer_state('sender', 'completed')

        except (ClientDisconnect, WebSocketDisconnect) as e:
            self.warning(f"△ Upload disconnected: {e}")
            await self.store.save_upload_progress(self.bytes_uploaded, last_chunk_id)
            await self.store.set_peer_state('sender', 'disconnected')
            # Don't wait for reconnection here, just save state

        except TimeoutError as e:
            self.warning(f"△ Timeout during upload.", exc_info=True)
            await self.store.save_upload_progress(self.bytes_uploaded, last_chunk_id)
            await on_error("Timeout during upload.")

        except TransferError as e:
            self.warning(f"△ Upload error: {e}")
            if e.propagate:
                await self.store.put_chunk(self.DEAD_FLAG)
            else:
                await on_error(e)

        finally:
            await anyio.sleep(1.0)

    async def supply_download(self, on_error: Callable[[Exception | str], Awaitable[None]], start_byte: int = 0) -> AsyncIterator[bytes]:
        """Supply download with resume support from specific byte position."""
        self.bytes_downloaded = start_byte
        last_chunk_id = '0'

        try:
            await self.store.set_peer_state('receiver', 'downloading')

            if start_byte > 0:
                self.info(f"▼ Resuming download from byte {start_byte}")
                chunk_id, offset = await self.store.find_chunk_for_byte_offset(start_byte)
                if chunk_id:
                    self.store._last_read_id = chunk_id
                    last_chunk_id = chunk_id

                    if offset > 0:
                        chunks = await self.store.get_chunks_from(chunk_id, count=1)
                        if chunks:
                            _, fields = chunks[0]
                            partial_data = fields[b'data'][offset:]
                            self.bytes_downloaded += len(partial_data)
                            yield partial_data

            while True:
                try:
                    chunk_id, chunk = await self.store.get_next_chunk(timeout=30.0)
                    last_chunk_id = chunk_id

                    if chunk == self.DEAD_FLAG:
                        await self.store.save_download_progress(self.bytes_downloaded, last_chunk_id)
                        await self.store.set_peer_state('receiver', 'sender_disconnected')
                        await self._wait_for_reconnection('receiver', on_error)
                        continue

                    if chunk == self.DONE_FLAG:
                        if self.bytes_downloaded < self.file.size:
                            raise TransferError("Received less data than expected.")
                        self.debug(f"▼ Done marker received, ending download.")
                        await self.store.set_peer_state('receiver', 'completed')
                        break

                    self.bytes_downloaded += len(chunk)
                    yield chunk

                    if self.bytes_downloaded % (64 * 1024) == 0:
                        await self.store.save_download_progress(self.bytes_downloaded, last_chunk_id)

                except TimeoutError:
                    self.info("▼ Timeout waiting for data, checking sender state...")
                    sender_state = await self.store.get_peer_state('sender')
                    if sender_state == 'disconnected':
                        await self._wait_for_reconnection('receiver', on_error)
                    else:
                        raise

        except TransferError as e:
            self.warning(f"▼ Download error: {e}")
            await self.store.save_download_progress(self.bytes_downloaded, last_chunk_id)
            await on_error(e)

        except Exception as e:
            self.error(f"▼ Unexpected download error!", exc_info=True)
            await self.store.save_download_progress(self.bytes_downloaded, last_chunk_id)
            await on_error(e)

    async def cleanup(self):
        try:
            with anyio.fail_after(30.0):
                await self.store.cleanup()
        except TimeoutError:
            self.warning(f"- Cleanup timed out.")
            pass

    async def _wait_for_reconnection(self, peer_type: str, on_error: Callable[[Exception | str], Awaitable[None]]) -> None:
        """Wait for peer to reconnect within timeout window."""
        self.info(f"◆ Waiting for {peer_type} to reconnect...")
        try:
            with anyio.fail_after(60.0):  # 60 second reconnection window
                while True:
                    state = await self.store.get_peer_state(peer_type)
                    if state in ['uploading', 'downloading']:
                        self.info(f"◆ {peer_type} reconnected!")
                        return
                    await anyio.sleep(1.0)
        except TimeoutError:
            self.warning(f"◆ {peer_type} did not reconnect in time")
            await on_error(f"{peer_type} disconnected and did not reconnect")
            raise TransferError(f"{peer_type} disconnected", propagate=True)

    async def get_resume_position(self) -> int:
        """Get the byte position to resume upload from."""
        progress = await self.store.get_upload_progress()
        if progress:
            return progress['bytes_uploaded']
        return 0

    async def finalize_download(self):
        """Finalize download and save progress."""
        if self.bytes_downloaded < self.file.size and not await self.is_interrupted():
            self.warning("▼ Client disconnected before download was complete.")
            progress = await self.store.get_download_progress()
            if progress:
                self.info(f"▼ Download progress saved at {self.bytes_downloaded} bytes")

        if self.bytes_downloaded >= self.file.size:
            await self.cleanup()
        else:
            self.debug("▼ Keeping transfer data for potential resumption")
