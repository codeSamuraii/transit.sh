import anyio
from typing import AsyncIterator, Optional
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
        stream_position = 0  # Current position in the stream we've read to
        bytes_sent = 0       # Bytes sent to client
        bytes_to_send = (end_byte - start_byte + 1) if end_byte else (self.file.size - start_byte)
        last_chunk_id = '0'
        is_range_request = end_byte is not None

        await self.store.set_receiver_state(ClientState.ACTIVE)

        if start_byte > 0:
            self.info(f"▼ Starting download from byte {start_byte}")
            if not is_range_request:
                # For live streams starting mid-file, check if we have previous progress
                progress = await self.store.get_download_progress()
                if progress and progress.bytes_downloaded >= start_byte:
                    last_chunk_id = progress.last_read_id
                    stream_position = progress.bytes_downloaded

        self.debug(f"▼ Range request: {start_byte}-{end_byte or 'end'}, to_send: {bytes_to_send}")

        try:
            while bytes_sent < bytes_to_send:
                try:
                    if is_range_request:
                        # For range requests, use non-blocking reads from existing stream data
                        result = await self.store.get_chunk_by_range(last_chunk_id)
                        if not result:
                            # Check if sender is still uploading
                            sender_state = await self.store.get_sender_state()
                            if sender_state == ClientState.COMPLETE:
                                # Upload is complete but no more chunks - we're done
                                break
                            elif sender_state == ClientState.DISCONNECTED:
                                if not await self._wait_for_reconnection("sender"):
                                    await self.store.set_receiver_state(ClientState.ERROR)
                                    return
                            await anyio.sleep(0.1)
                            continue
                        chunk_id, chunk_data = result
                    else:
                        # For live streams, use blocking reads
                        chunk_id, chunk_data = await self.store.get_next_chunk(
                            timeout=self.STREAM_TIMEOUT,
                            last_id=last_chunk_id
                        )

                    last_chunk_id = chunk_id

                    if chunk_data == self.DONE_FLAG:
                        self.debug("▼ Done marker received")
                        await self.store.set_receiver_state(ClientState.COMPLETE)
                        break
                    elif chunk_data == self.DEAD_FLAG:
                        self.warning("▼ Dead marker received")
                        await self.store.set_receiver_state(ClientState.ERROR)
                        return

                    # Skip bytes until we reach start_byte
                    if stream_position < start_byte:
                        bytes_in_chunk = len(chunk_data)
                        skip = min(bytes_in_chunk, start_byte - stream_position)
                        chunk_data = chunk_data[skip:]
                        stream_position += skip

                        # If we still haven't reached start_byte, move to next chunk
                        if stream_position < start_byte:
                            stream_position += len(chunk_data)
                            continue

                    # Send only the bytes we need for this range
                    if len(chunk_data) > 0:
                        remaining = bytes_to_send - bytes_sent
                        if len(chunk_data) > remaining:
                            chunk_data = chunk_data[:remaining]

                        yield chunk_data
                        bytes_sent += len(chunk_data)
                        stream_position += len(chunk_data)

                        # Save progress periodically for resumption
                        if stream_position % (64 * 1024) == 0:
                            await self.store.save_download_progress(
                                bytes_downloaded=stream_position,
                                last_read_id=last_chunk_id
                            )

                except TimeoutError:
                    self.info("▼ Timeout waiting for data")
                    sender_state = await self.store.get_sender_state()
                    if sender_state == ClientState.DISCONNECTED:
                        if not await self._wait_for_reconnection("sender"):
                            await self.store.set_receiver_state(ClientState.ERROR)
                            return
                    else:
                        raise

            # Determine completion status
            if is_range_request:
                # For range requests, just log completion but don't mark transfer as complete
                # Multiple ranges may be downloading different parts of the same file
                self.info(f"▼ Range download complete ({bytes_sent} bytes from {start_byte}-{end_byte or 'end'})")
            else:
                # For full downloads, check if entire file was downloaded
                total_downloaded = start_byte + bytes_sent
                if total_downloaded >= self.file.size:
                    self.info("▼ Full download complete")
                    await self.store.set_receiver_state(ClientState.COMPLETE)
                else:
                    self.info(f"▼ Download incomplete ({total_downloaded}/{self.file.size} bytes)")
                    await self.store.save_download_progress(
                        bytes_downloaded=stream_position,
                        last_read_id=last_chunk_id
                    )

        except (ConnectionError, WebSocketDisconnect) as e:
            self.warning(f"▼ Download disconnected: {e}")
            await self.store.save_download_progress(
                bytes_downloaded=stream_position,
                last_read_id=last_chunk_id
            )
            await self.store.set_receiver_state(ClientState.DISCONNECTED)

            if not await self._wait_for_reconnection("receiver"):
                await self.store.set_receiver_state(ClientState.ERROR)
                await self.set_interrupted()

        except Exception as e:
            self.error(f"▼ Unexpected download error: {e}", exc_info=True)
            await self.store.set_receiver_state(ClientState.ERROR)
            await self.set_interrupted()

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