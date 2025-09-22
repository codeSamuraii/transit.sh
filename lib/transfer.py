import anyio
from typing import AsyncIterator, Optional, Tuple
from fastapi import WebSocketDisconnect

from lib.store import Store
from lib.metadata import FileMetadata
from lib.models import ClientState
from lib.logging import HasLogging


class FileTransfer(metaclass=HasLogging, name_from='uid'):
    """Handles bidirectional file streaming between sender and receiver."""

    DONE_FLAG = b'\x00\xFF'
    DEAD_FLAG = b'\xDE\xAD'

    STREAM_TIMEOUT = 30.0
    RECONNECT_TIMEOUT = 60.0
    RECONNECT_POLL_INTERVAL = 1.0

    def __init__(self, uid: str, file: FileMetadata):
        self.uid = self._format_uid(uid)
        self.file = file
        self.store = Store(self.uid)

    @classmethod
    async def create(cls, uid: str, file: FileMetadata):
        """Create a new transfer."""
        transfer = cls(uid, file)
        await transfer.store.set_metadata(file.to_json())
        return transfer

    @classmethod
    async def get(cls, uid: str):
        """Get an existing transfer."""
        store = Store(uid)
        metadata_json = await store.get_metadata()
        if not metadata_json:
            raise KeyError(f"Transfer '{uid}' not found")

        file = FileMetadata.from_json(metadata_json)
        return cls(uid, file)

    @staticmethod
    def _format_uid(uid: str):
        return str(uid).strip().encode('ascii', 'ignore').decode()

    def get_file_info(self):
        """Get file information tuple."""
        return self.file.name, self.file.size, self.file.type

    async def wait_for_event(self, event_name: str, timeout: float = 300.0):
        """Wait for a specific event."""
        await self.store.wait_for_event(event_name, timeout)

    async def set_client_connected(self):
        """Notify sender that receiver is connected."""
        self.debug("▼ Notifying sender that receiver is connected...")
        await self.store.set_event('client_connected')

    async def wait_for_client_connected(self):
        """Wait for receiver to connect."""
        self.info("△ Waiting for client to connect...")
        await self.wait_for_event('client_connected')
        self.debug("△ Received client connected notification")

    async def is_receiver_connected(self) -> bool:
        """Check if receiver is connected."""
        return await self.store.is_receiver_connected()

    async def set_receiver_connected(self) -> bool:
        """Mark receiver as connected (atomic)."""
        return await self.store.set_receiver_connected()

    async def is_interrupted(self) -> bool:
        """Check if transfer was interrupted."""
        return await self.store.is_interrupted()

    async def set_interrupted(self):
        """Mark transfer as interrupted."""
        await self.store.set_interrupted()

    async def is_completed(self) -> bool:
        """Check if transfer is completed."""
        return await self.store.is_completed()

    async def set_completed(self):
        """Mark transfer as completed."""
        await self.store.set_completed()

    async def get_resume_position(self) -> int:
        """Get the byte position to resume upload from."""
        progress = await self.store.get_upload_progress()
        return progress.bytes_uploaded if progress else 0

    async def _wait_for_reconnection(self, peer_type: str) -> bool:
        """Wait for a peer to reconnect. Returns True if reconnected, False if timed out."""
        self.info(f"◆ Waiting for {peer_type} to reconnect...")

        try:
            with anyio.fail_after(self.RECONNECT_TIMEOUT):
                while True:
                    if peer_type == "sender":
                        state = await self.store.get_sender_state()
                    else:
                        state = await self.store.get_receiver_state()

                    if state == ClientState.ACTIVE:
                        self.info(f"◆ {peer_type.capitalize()} reconnected!")
                        return True

                    await anyio.sleep(self.RECONNECT_POLL_INTERVAL)
        except TimeoutError:
            self.warning(f"◆ {peer_type.capitalize()} did not reconnect in time")
            return False

    async def _get_next_chunk(self, last_chunk_id: str, is_range_request: bool) -> Optional[Tuple[str, bytes]]:
        """Get next chunk from stream. Returns None if no more data available."""
        if is_range_request:
            result = await self.store.get_chunk_by_range(last_chunk_id)
            if not result:
                if not await self._should_wait_for_sender():
                    return None
                return ('wait', None)
            return result
        else:
            return await self.store.get_next_chunk(self.STREAM_TIMEOUT, last_id=last_chunk_id)

    async def _should_wait_for_sender(self) -> bool:
        """Check if we should wait for sender to reconnect or give up."""
        sender_state = await self.store.get_sender_state()
        if sender_state == ClientState.COMPLETE:
            return False
        elif sender_state == ClientState.DISCONNECTED:
            if not await self._wait_for_reconnection("sender"):
                await self.store.set_receiver_state(ClientState.ERROR)
                return False
        return True

    def _adjust_chunk_for_range(self, chunk_data: bytes, stream_position: int,
                                start_byte: int, bytes_sent: int, bytes_to_send: int) -> Tuple[Optional[bytes], int]:
        """Adjust chunk data for byte range. Returns (data_to_send, new_stream_position)."""
        new_position = stream_position

        # Skip bytes before start_byte
        if stream_position < start_byte:
            skip = min(len(chunk_data), start_byte - stream_position)
            chunk_data = chunk_data[skip:]
            new_position += skip

            # Still haven't reached start? Skip entire chunk
            if new_position < start_byte:
                new_position += len(chunk_data)
                return None, new_position

        # Trim to remaining bytes needed
        if chunk_data and bytes_sent + len(chunk_data) > bytes_to_send:
            chunk_data = chunk_data[:bytes_to_send - bytes_sent]

        return chunk_data if chunk_data else None, new_position

    async def _save_progress_if_needed(self, stream_position: int, last_chunk_id: str, force: bool = False):
        """Save download progress periodically or when forced."""
        if force or stream_position % (64 * 1024) == 0:
            await self.store.save_download_progress(
                bytes_downloaded=stream_position,
                last_read_id=last_chunk_id
            )
            if force:
                self.debug(f"▼ Progress saved: {stream_position} bytes")

    async def _initialize_download_state(self, start_byte: int, is_range_request: bool) -> Tuple[int, str]:
        """Initialize download state and return (stream_position, last_chunk_id)."""
        stream_position = 0
        last_chunk_id = '0'

        if start_byte > 0:
            self.info(f"▼ Starting download from byte {start_byte}")
            if not is_range_request:
                progress = await self.store.get_download_progress()
                if progress and progress.bytes_downloaded >= start_byte:
                    last_chunk_id = progress.last_read_id
                    stream_position = progress.bytes_downloaded

        return stream_position, last_chunk_id

    async def _finalize_download_status(self, bytes_sent: int, stream_position: int,
                                       start_byte: int, end_byte: Optional[int],
                                       last_chunk_id: str):
        """Update final download status based on what was transferred."""
        if end_byte is not None:
            self.info(f"▼ Range download complete ({bytes_sent} bytes from {start_byte}-{end_byte})")
            return

        total_downloaded = start_byte + bytes_sent
        if total_downloaded >= self.file.size:
            self.info("▼ Full download complete")
            await self.store.set_receiver_state(ClientState.COMPLETE)
        else:
            self.info(f"▼ Download incomplete ({total_downloaded}/{self.file.size} bytes)")
            await self._save_progress_if_needed(stream_position, last_chunk_id, force=True)

    async def _handle_download_disconnect(self, error: Exception, stream_position: int, last_chunk_id: str):
        """Handle download disconnection errors."""
        self.warning(f"▼ Download disconnected: {error}")
        await self.store.save_download_progress(
            bytes_downloaded=stream_position,
            last_read_id=last_chunk_id
        )
        await self.store.set_receiver_state(ClientState.DISCONNECTED)

        if not await self._wait_for_reconnection("receiver"):
            await self.store.set_receiver_state(ClientState.ERROR)
            await self.set_interrupted()

    async def _handle_download_timeout(self, stream_position: int, last_chunk_id: str):
        """Handle download timeout by checking sender state."""
        self.info("▼ Timeout waiting for data")
        sender_state = await self.store.get_sender_state()
        if sender_state == ClientState.DISCONNECTED:
            if not await self._wait_for_reconnection("sender"):
                await self.store.set_receiver_state(ClientState.ERROR)
                return False
        else:
            raise TimeoutError("Download timeout")
        return True

    async def _handle_download_fatal_error(self, error: Exception):
        """Handle unexpected download errors."""
        self.error(f"▼ Unexpected download error: {error}", exc_info=True)
        await self.store.set_receiver_state(ClientState.ERROR)
        await self.set_interrupted()

    async def collect_upload(self, stream: AsyncIterator[bytes], resume_from: int = 0) -> None:
        """Collect file data from sender and store in Redis stream."""
        bytes_uploaded = resume_from
        last_chunk_id = '0'

        if resume_from > 0:
            self.info(f"△ Resuming upload from byte {resume_from}")
            progress = await self.store.get_upload_progress()
            if progress:
                last_chunk_id = progress.last_chunk_id

        await self.store.set_sender_state(ClientState.ACTIVE)

        try:
            chunk_count = 0
            async for chunk in stream:
                if chunk == b'':
                    self.debug("△ Empty chunk received, ending upload")
                    break

                if await self.is_interrupted():
                    self.info("△ Transfer interrupted by receiver")
                    # Save progress before changing state
                    await self.store.save_upload_progress(bytes_uploaded=bytes_uploaded, last_chunk_id=last_chunk_id)
                    await self.store.set_sender_state(ClientState.DISCONNECTED)

                    if not await self._wait_for_reconnection("receiver"):
                        await self.store.set_sender_state(ClientState.ERROR)
                        return

                    await self.store.set_sender_state(ClientState.ACTIVE)

                # Store chunk and update progress
                last_chunk_id = await self.store.put_chunk(chunk)
                bytes_uploaded += len(chunk)
                chunk_count += 1

                # Save progress more frequently for better resumption
                # Save every 4KB (every chunk in most tests) or every 16KB whichever comes first
                if chunk_count % 1 == 0 or bytes_uploaded % (16 * 1024) == 0:
                    await self.store.save_upload_progress(bytes_uploaded=bytes_uploaded, last_chunk_id=last_chunk_id)
                    self.debug(f"△ Progress saved: {bytes_uploaded} bytes, chunk {last_chunk_id}")

            # Final progress save and completion handling
            await self.store.save_upload_progress(bytes_uploaded=bytes_uploaded, last_chunk_id=last_chunk_id)

            if bytes_uploaded >= self.file.size:
                self.debug("△ Upload complete, sending done marker")
                await self.store.put_chunk(self.DONE_FLAG)
                await self.store.set_sender_state(ClientState.COMPLETE)
                self.info("△ Upload complete")
            else:
                self.info(f"△ Upload incomplete ({bytes_uploaded}/{self.file.size} bytes)")
                await self.store.set_sender_state(ClientState.DISCONNECTED)

        except (WebSocketDisconnect, ConnectionError, TimeoutError) as e:
            self.warning(f"△ Upload disconnected: {e}")
            # Save progress immediately on disconnect
            try:
                await self.store.save_upload_progress(bytes_uploaded=bytes_uploaded, last_chunk_id=last_chunk_id)
                self.debug(f"△ Progress saved on disconnect: {bytes_uploaded} bytes")
            except Exception as save_error:
                self.error(f"△ Failed to save progress on disconnect: {save_error}")

            await self.store.set_sender_state(ClientState.DISCONNECTED)

            # Note: this is the sender, so we don't wait for reconnection here
            # The receiver will handle the wait-for-reconnection when it detects sender disconnection

        except Exception as e:
            self.error(f"△ Unexpected upload error: {e}", exc_info=True)
            # Save progress before marking as error
            try:
                await self.store.save_upload_progress(bytes_uploaded=bytes_uploaded, last_chunk_id=last_chunk_id)
            except:
                pass
            await self.store.set_sender_state(ClientState.ERROR)
            await self.store.put_chunk(self.DEAD_FLAG)

    async def supply_download(self, start_byte: int = 0, end_byte: Optional[int] = None) -> AsyncIterator[bytes]:
        """Stream file data to the receiver."""
        bytes_sent = 0
        bytes_to_send = (end_byte - start_byte + 1) if end_byte else (self.file.size - start_byte)
        is_range_request = end_byte is not None

        stream_position, last_chunk_id = await self._initialize_download_state(start_byte, is_range_request)
        await self.store.set_receiver_state(ClientState.ACTIVE)

        self.debug(f"▼ Range request: {start_byte}-{end_byte or 'end'}, to_send: {bytes_to_send}")

        try:
            while bytes_sent < bytes_to_send:
                # Get next chunk
                result = await self._get_next_chunk(last_chunk_id, is_range_request)
                if result is None:
                    break
                if result[0] == 'wait':
                    await anyio.sleep(0.1)
                    continue

                chunk_id, chunk_data = result
                last_chunk_id = chunk_id

                # Check for control flags
                if chunk_data == self.DONE_FLAG:
                    self.debug("▼ Done marker received")
                    await self.store.set_receiver_state(ClientState.COMPLETE)
                    break
                elif chunk_data == self.DEAD_FLAG:
                    self.warning("▼ Dead marker received")
                    await self.store.set_receiver_state(ClientState.ERROR)
                    return

                # Process chunk for byte range
                chunk_to_send, stream_position = self._adjust_chunk_for_range(
                    chunk_data, stream_position, start_byte, bytes_sent, bytes_to_send
                )

                # Yield data if we have any
                if chunk_to_send:
                    yield chunk_to_send
                    bytes_sent += len(chunk_to_send)
                    await self._save_progress_if_needed(stream_position, last_chunk_id)

            # Handle completion
            await self._finalize_download_status(
                bytes_sent, stream_position, start_byte, end_byte, last_chunk_id
            )

        except TimeoutError:
            if not await self._handle_download_timeout(stream_position, last_chunk_id):
                return
        except (ConnectionError, WebSocketDisconnect) as e:
            await self._handle_download_disconnect(e, stream_position, last_chunk_id)
        except Exception as e:
            await self._handle_download_fatal_error(e)

    async def finalize_download(self):
        """Finalize download and potentially clean up."""
        receiver_state = await self.store.get_receiver_state()

        if receiver_state == ClientState.COMPLETE:
            await self.cleanup()
        elif receiver_state == ClientState.DISCONNECTED:
            self.info("▼ Keeping transfer data for potential resumption")
        else:
            self.debug("▼ Download finalized")

    async def cleanup(self):
        """Clean up transfer data from Redis."""
        try:
            with anyio.fail_after(30.0):
                await self.store.cleanup()
        except TimeoutError:
            self.warning("Cleanup timed out")


def parse_range_header(range_header: str, file_size: int) -> Optional[dict]:
    """Parse HTTP Range header and return range details."""
    if not range_header or not range_header.startswith('bytes='):
        return None

    try:
        range_spec = range_header[6:]
        if '-' in range_spec:
            start_str, end_str = range_spec.split('-', 1)

            if not start_str and end_str:
                suffix_length = int(end_str)
                start = max(0, file_size - suffix_length)
                end = file_size - 1
            elif start_str and not end_str:
                start = int(start_str)
                end = file_size - 1
            elif start_str and end_str:
                start = int(start_str)
                end = int(end_str)
            else:
                return None

            if start >= file_size or start < 0 or start > end:
                return None

            if end >= file_size:
                end = file_size - 1

            return {
                'start': start,
                'end': end,
                'length': end - start + 1
            }
    except (ValueError, IndexError):
        pass

    return None


def format_content_range(start: int, end: int, total: int) -> str:
    """Format Content-Range header value."""
    return f"bytes {start}-{end}/{total}"