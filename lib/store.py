import random
import anyio
import redis.asyncio as redis
from redis.asyncio.client import PubSub
from typing import Optional, Tuple

from lib.logging import HasLogging


class Store(metaclass=HasLogging, name_from='transfer_id'):
    """
    Redis Stream-based store for resumable file transfers.
    Handles data streaming, progress tracking, and event signaling.
    """

    redis_client: None | redis.Redis = None

    def __init__(self, transfer_id: str):
        self.transfer_id = transfer_id
        self.redis = self.get_redis()

        self._stream_key = f'stream:{transfer_id}'
        self._progress_key = f'progress:{transfer_id}'
        self._state_key = f'state:{transfer_id}'
        self._k_meta = self.key('metadata')
        self._k_cleanup = f'cleanup:{transfer_id}'
        self._k_receiver_connected = self.key('receiver_connected')

        self._last_read_id = '0'  # Track last read position for downloads

    @classmethod
    def get_redis(cls) -> redis.Redis:
        """Get the Redis client instance."""
        if cls.redis_client is None:
            from app import app
            cls.redis_client = app.state.redis
        return cls.redis_client

    def key(self, name: str) -> str:
        """Get the Redis key for this transfer with the provided name."""
        return f'transfer:{self.transfer_id}:{name}'

    ## Stream operations ##

    async def _wait_for_stream_space(self, maxsize: int) -> None:
        """Wait until stream has space for new chunks."""
        while await self.redis.xlen(self._stream_key) >= maxsize:
            await anyio.sleep(0.5)

    async def put_chunk(self, data: bytes, maxsize: int = 16, timeout: float = 20.0) -> str:
        """Add chunk to stream with backpressure control. Returns stream ID."""
        with anyio.fail_after(timeout):
            await self._wait_for_stream_space(maxsize)

        fields = {'data': data, 'size': len(data)}
        stream_id = await self.redis.xadd(self._stream_key, fields, maxlen=1000, approximate=True)
        return stream_id

    async def get_next_chunk(self, timeout: float = 20.0) -> Tuple[str, bytes]:
        """Get next chunk from stream with timeout. Returns (chunk_id, data)."""
        params = {self._stream_key: self._last_read_id}

        result = await self.redis.xread(params, count=1, block=int(timeout * 1000))
        if not result:
            raise TimeoutError("Timeout waiting for data")

        stream_name, messages = result[0]
        chunk_id, fields = messages[0]
        self._last_read_id = chunk_id

        return chunk_id, fields[b'data']

    async def get_chunks_from(self, start_id: str, count: Optional[int] = None) -> list:
        """Get chunks starting from a specific stream ID."""
        return await self.redis.xrange(self._stream_key, min=start_id, max='+', count=count)

    ## Event operations ##

    async def set_event(self, event_name: str, expiry: float = 300.0) -> None:
        """Set an event flag for this transfer."""
        event_key = self.key(event_name)
        event_marker_key = f'{event_key}:marker'

        await self.redis.set(event_marker_key, '1', ex=int(expiry))
        await self.redis.publish(event_key, '1')

    async def _poll_marker(self, event_key: str) -> None:
        """Poll for event marker existence."""
        event_marker_key = f'{event_key}:marker'
        while not await self.redis.exists(event_marker_key):
            await anyio.sleep(1)

    async def _listen_for_message(self, pubsub: PubSub, event_key: str) -> None:
        """Listen for pubsub messages."""
        await pubsub.subscribe(event_key)
        async for message in pubsub.listen():
            if message and message['type'] == 'message':
                return

    async def wait_for_event(self, event_name: str, timeout: float = 300.0) -> None:
        """Wait for an event to be set for this transfer."""
        event_key = self.key(event_name)
        pubsub = self.redis.pubsub(ignore_subscribe_messages=True)

        try:
            with anyio.fail_after(timeout):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(self._poll_marker, event_key)
                    tg.start_soon(self._listen_for_message, pubsub, event_key)

        except TimeoutError:
            self.error(f"Timeout waiting for event '{event_name}' after {timeout} seconds.")
            raise

        finally:
            await pubsub.unsubscribe(event_key)
            await pubsub.aclose()

    ## Metadata operations ##

    async def set_metadata(self, metadata: str) -> None:
        """Store transfer metadata."""
        challenge = random.randbytes(8)
        await self.redis.set(self._k_meta, challenge, nx=True)
        if await self.redis.get(self._k_meta) == challenge:
            await self.redis.set(self._k_meta, metadata, ex=300)
        else:
            raise KeyError("Metadata already set for this transfer.")

    async def get_metadata(self) -> str | None:
        """Retrieve transfer metadata."""
        return await self.redis.get(self._k_meta)

    ## Transfer state operations ##

    async def set_receiver_connected(self) -> bool:
        """
        Mark that a receiver has connected for this transfer.
        Returns True if the flag was set, False if it was already created.
        """
        return bool(await self.redis.set(self._k_receiver_connected, '1', ex=300, nx=True))

    async def is_receiver_connected(self) -> bool:
        """Check if a receiver has already connected."""
        return await self.redis.exists(self._k_receiver_connected) > 0

    async def set_completed(self) -> None:
        """Mark the transfer as completed."""
        await self.redis.set(f'completed:{self.transfer_id}', '1', ex=300, nx=True)

    async def is_completed(self) -> bool:
        """Check if the transfer is marked as completed."""
        return await self.redis.exists(f'completed:{self.transfer_id}') > 0

    async def set_interrupted(self) -> None:
        """Mark the transfer as interrupted but keep stream data for resumption."""
        await self.redis.set(f'interrupt:{self.transfer_id}', '1', ex=3600, nx=True)

    async def is_interrupted(self) -> bool:
        """Check if the transfer was interrupted."""
        return await self.redis.exists(f'interrupt:{self.transfer_id}') > 0

    ## Cleanup operations ##

    async def cleanup_started(self) -> bool:
        """
        Check if cleanup has already been initiated for this transfer.
        This uses a set/get pattern with challenge to avoid race conditions.
        """
        challenge = random.randbytes(8)
        await self.redis.set(self._k_cleanup, challenge, ex=60, nx=True)
        if await self.redis.get(self._k_cleanup) == challenge:
            return False
        return True

    ## Progress tracking ##

    async def save_upload_progress(self, bytes_uploaded: int, last_chunk_id: str) -> None:
        """Save upload progress for resumption."""
        await self.redis.hset(self._progress_key, mapping={
            'bytes_uploaded': bytes_uploaded,
            'last_chunk_id': last_chunk_id
        })
        await self.redis.expire(self._progress_key, 3600)

    async def get_upload_progress(self) -> Optional[dict]:
        """Get saved upload progress."""
        progress = await self.redis.hgetall(self._progress_key)
        if progress:
            return {
                'bytes_uploaded': int(progress.get(b'bytes_uploaded', 0)),
                'last_chunk_id': progress.get(b'last_chunk_id', b'0').decode()
            }
        return None

    async def save_download_progress(self, bytes_downloaded: int, last_chunk_id: str) -> None:
        """Save download progress for resumption."""
        await self.redis.hset(self._progress_key, mapping={
            'bytes_downloaded': bytes_downloaded,
            'last_read_id': last_chunk_id
        })
        await self.redis.expire(self._progress_key, 3600)

    async def get_download_progress(self) -> Optional[dict]:
        """Get saved download progress."""
        progress = await self.redis.hgetall(self._progress_key)
        if progress:
            return {
                'bytes_downloaded': int(progress.get(b'bytes_downloaded', 0)),
                'last_read_id': progress.get(b'last_read_id', b'0').decode()
            }
        return None

    async def set_peer_state(self, peer_type: str, state: str) -> None:
        """Set the state of a peer (sender/receiver)."""
        await self.redis.hset(self._state_key, peer_type, state)
        await self.redis.expire(self._state_key, 3600)

    async def get_peer_state(self, peer_type: str) -> Optional[str]:
        """Get the state of a peer."""
        state = await self.redis.hget(self._state_key, peer_type)
        return state.decode() if state else None

    async def find_chunk_for_byte_offset(self, byte_offset: int) -> Tuple[Optional[str], int]:
        """Find chunk ID and offset within chunk for a byte position."""
        cursor = '-'
        total_bytes = 0

        while True:
            chunks = await self.redis.xrange(self._stream_key, min=cursor, max='+', count=100)
            if not chunks:
                break

            for chunk_id, fields in chunks:
                chunk_size = int(fields.get(b'size', 0))
                if total_bytes + chunk_size > byte_offset:
                    return chunk_id, byte_offset - total_bytes
                total_bytes += chunk_size
                cursor = f'({chunk_id}'

        return None, byte_offset

    async def cleanup(self) -> int:
        """Remove all keys related to this transfer."""
        if await self.cleanup_started():
            return 0

        pattern = self.key('*')
        keys_to_delete = {self._stream_key, self._progress_key, self._state_key}

        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern)
            keys_to_delete |= set(keys)
            if cursor == 0:
                break

        if keys_to_delete:
            self.debug(f"- Cleaning up {len(keys_to_delete)} keys")
            return await self.redis.delete(*keys_to_delete)
        return 0
