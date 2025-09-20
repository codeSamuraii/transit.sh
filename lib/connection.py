import anyio
from typing import Optional, Dict, Any
from datetime import datetime
from lib.logging import HasLogging, get_logger
from lib.store import Store

logger = get_logger('connection')


class ConnectionManager(metaclass=HasLogging, name_from='transfer_id'):
    """Manages connection states and reconnection logic for resumable transfers."""

    RECONNECT_WINDOW = 60.0  # seconds
    KEEPALIVE_INTERVAL = 10.0  # seconds

    def __init__(self, transfer_id: str, store: Store):
        self.transfer_id = transfer_id
        self.store = store
        self.peer_states: Dict[str, Any] = {}

    async def register_peer(self, peer_type: str, connection_info: Dict[str, Any]) -> None:
        """Register a peer connection."""
        state_data = {
            'status': 'connected',
            'timestamp': datetime.now().isoformat(),
            **connection_info
        }
        await self.store.set_peer_state(peer_type, 'connected')
        self.peer_states[peer_type] = state_data
        self.info(f"Registered {peer_type} connection")

    async def handle_disconnect(self, peer_type: str) -> bool:
        """Handle peer disconnection and return whether to wait for reconnection."""
        await self.store.set_peer_state(peer_type, 'disconnected')

        other_peer = 'receiver' if peer_type == 'sender' else 'sender'
        other_state = await self.store.get_peer_state(other_peer)

        if other_state in ['connected', 'uploading', 'downloading']:
            self.info(f"{peer_type} disconnected, keeping {other_peer} connected")
            return True  # Wait for reconnection

        self.warning(f"Both peers disconnected, may abandon transfer")
        return False

    async def wait_for_reconnection(self, peer_type: str, timeout: Optional[float] = None) -> bool:
        """Wait for a peer to reconnect within timeout."""
        timeout = timeout or self.RECONNECT_WINDOW
        self.info(f"Waiting up to {timeout}s for {peer_type} to reconnect...")

        try:
            with anyio.fail_after(timeout):
                while True:
                    state = await self.store.get_peer_state(peer_type)
                    if state in ['connected', 'uploading', 'downloading', 'resuming']:
                        self.info(f"{peer_type} reconnected successfully")
                        return True
                    await anyio.sleep(1.0)
        except TimeoutError:
            self.warning(f"{peer_type} did not reconnect within {timeout}s")
            return False

    async def handle_reconnection(self, peer_type: str) -> Dict[str, Any]:
        """Handle peer reconnection and return resume info."""
        await self.store.set_peer_state(peer_type, 'resuming')

        # Check if other peer is waiting
        other_peer = 'receiver' if peer_type == 'sender' else 'sender'
        other_state = await self.store.get_peer_state(other_peer)

        resume_info = {}
        if peer_type == 'sender':
            progress = await self.store.get_upload_progress()
            if progress:
                resume_info['bytes_uploaded'] = progress['bytes_uploaded']
                resume_info['last_chunk_id'] = progress['last_chunk_id']
        else:
            progress = await self.store.get_download_progress()
            if progress:
                resume_info['bytes_downloaded'] = progress['bytes_downloaded']
                resume_info['last_read_id'] = progress['last_read_id']

        resume_info['other_peer_state'] = other_state
        resume_info['can_resume'] = True

        self.info(f"{peer_type} reconnection handled, resume info: {resume_info}")
        return resume_info

    async def keepalive_loop(self, peer_type: str) -> None:
        """Send keepalive signals to maintain connection state."""
        while True:
            try:
                await anyio.sleep(self.KEEPALIVE_INTERVAL)
                state = await self.store.get_peer_state(peer_type)
                if state not in ['connected', 'uploading', 'downloading']:
                    break
                # Update timestamp to show peer is still alive
                await self.store.set_peer_state(peer_type, state)
            except Exception as e:
                self.error(f"Keepalive error for {peer_type}: {e}")
                break

    async def check_peer_health(self, peer_type: str) -> bool:
        """Check if a peer connection is healthy."""
        state = await self.store.get_peer_state(peer_type)
        return state in ['connected', 'uploading', 'downloading', 'resuming']

    async def coordinate_resume(self) -> bool:
        """Coordinate resume between both peers."""
        sender_state = await self.store.get_peer_state('sender')
        receiver_state = await self.store.get_peer_state('receiver')

        if sender_state in ['resuming', 'connected'] and receiver_state in ['resuming', 'connected']:
            self.info("Both peers ready to resume transfer")
            await self.store.set_event('resume_transfer')
            return True

        self.debug(f"Cannot resume yet - sender: {sender_state}, receiver: {receiver_state}")
        return False

    async def cleanup_on_error(self) -> None:
        """Clean up connection states on error."""
        await self.store.set_peer_state('sender', 'error')
        await self.store.set_peer_state('receiver', 'error')
        self.warning("Connection states cleaned up due to error")