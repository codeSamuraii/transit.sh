from typing import Optional, Tuple
from lib.store import Store
from lib.metadata import FileMetadata
from lib.logging import HasLogging, get_logger

logger = get_logger('resume')


class ResumptionHandler(metaclass=HasLogging, name_from='transfer_id'):
    """Handles transfer resumption logic."""

    def __init__(self, transfer_id: str, store: Store):
        self.transfer_id = transfer_id
        self.store = store

    async def can_resume_upload(self) -> bool:
        """Check if an upload can be resumed."""
        progress = await self.store.get_upload_progress()
        if not progress:
            return False

        sender_state = await self.store.get_peer_state('sender')
        return sender_state in ['paused', 'disconnected', 'incomplete']

    async def can_resume_download(self) -> bool:
        """Check if a download can be resumed."""
        progress = await self.store.get_download_progress()
        if not progress:
            return False

        receiver_state = await self.store.get_peer_state('receiver')
        return receiver_state in ['paused', 'disconnected', 'sender_disconnected']

    async def get_upload_resume_info(self) -> Tuple[int, str]:
        """Get upload resume position and last chunk ID."""
        progress = await self.store.get_upload_progress()
        if progress:
            return progress['bytes_uploaded'], progress['last_chunk_id']
        return 0, '0'

    async def get_download_resume_info(self) -> Tuple[int, str]:
        """Get download resume position and last read ID."""
        progress = await self.store.get_download_progress()
        if progress:
            return progress['bytes_downloaded'], progress['last_read_id']
        return 0, '0'

    async def prepare_upload_resume(self) -> dict:
        """Prepare upload for resumption and return resume info."""
        bytes_uploaded, last_chunk_id = await self.get_upload_resume_info()

        await self.store.set_peer_state('sender', 'resuming')

        return {
            'resume_from': bytes_uploaded,
            'last_chunk_id': last_chunk_id,
            'can_resume': True
        }

    async def prepare_download_resume(self, range_header: Optional[str] = None) -> dict:
        """Prepare download for resumption and return resume info."""
        if range_header:
            start_byte = self._parse_range_header(range_header)
        else:
            bytes_downloaded, _ = await self.get_download_resume_info()
            start_byte = bytes_downloaded

        await self.store.set_peer_state('receiver', 'resuming')

        return {
            'start_byte': start_byte,
            'can_resume': True,
            'total_size': None  # Will be filled from metadata
        }

    def _parse_range_header(self, range_header: str) -> int:
        """Parse Range header to get start byte position."""
        if not range_header or not range_header.startswith('bytes='):
            return 0

        try:
            range_spec = range_header[6:]  # Remove 'bytes='
            if '-' in range_spec:
                start, end = range_spec.split('-', 1)
                return int(start) if start else 0
        except (ValueError, IndexError):
            pass

        return 0

    async def validate_resume_request(self, file: FileMetadata) -> bool:
        """Validate that resume request matches original transfer."""
        stored_metadata = await self.store.get_metadata()
        if not stored_metadata:
            return False

        try:
            stored_file = FileMetadata.from_json(stored_metadata)
            return (stored_file.name == file.name and
                    stored_file.size == file.size and
                    stored_file.type == file.type)
        except Exception:
            return False

    async def handle_peer_reconnection(self, peer_type: str) -> None:
        """Handle when a peer reconnects."""
        other_peer = 'receiver' if peer_type == 'sender' else 'sender'
        other_state = await self.store.get_peer_state(other_peer)

        if other_state == 'waiting':
            self.info(f"Both peers reconnected, resuming transfer")
            await self.store.set_event('resume_transfer')

    async def cleanup_stale_transfers(self, max_age_seconds: int = 3600) -> None:
        """Clean up stale transfer data older than max_age."""
        # This would be called periodically to clean up abandoned transfers
        # Implementation depends on Redis TTL or timestamp tracking
        pass