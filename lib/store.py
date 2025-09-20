import random
import anyio
import redis.asyncio as redis
from redis.asyncio.client import PubSub
from typing import Optional, Tuple

from lib.logging import HasLogging
from lib.models import UploadProgress, DownloadProgress, ClientState


class Store(metaclass=HasLogging, name_from='transfer_id'):
    """Redis Stream-based store for file transfers."""

    redis_client: Optional[redis.Redis] = None

    # Expiry times
    METADATA_EXPIRY = 300
    EVENT_EXPIRY = 300
    PROGRESS_EXPIRY = 3600
    STATE_EXPIRY = 3600
    CLEANUP_LOCK_EXPIRY = 60

    def __init__(self, transfer_id: str):
        self.transfer_id = transfer_id
        self.redis = self.get_redis()

        self._stream_key = f'transfer:{transfer_id}:queue'
        self._progress_key = f'transfer:{transfer_id}:progress'
        self._state_key = f'transfer:{transfer_id}:state'
        self._k_meta = f'transfer:{transfer_id}:metadata'
        self._k_cleanup = f'transfer:{transfer_id}:cleanup'
        self._k_receiver_connected = f'transfer:{transfer_id}:receiver_connected'

    @classmethod
    def get_redis(cls) -> redis.Redis:
        """Get Redis client instance."""
        if cls.redis_client is None:
            from app import app
            cls.redis_client = app.state.redis
        return cls.redis_client

    async def put_chunk(self, data: bytes, timeout: float = 30.0) -> str:
        """Add a chunk to the stream."""
        fields = {'data': data, 'size': len(data)}
        with anyio.fail_after(timeout):
            stream_id = await self.redis.xadd(self._stream_key, fields)
        return stream_id

    async def get_next_chunk(self, timeout: float = 30.0, last_id: str = '0') -> Tuple[str, bytes]:
        """Read the next chunk from the stream (blocking)."""
        params = {self._stream_key: last_id}
        result = await self.redis.xread(params, count=1, block=int(timeout * 1000))
        if not result:
            raise TimeoutError("Timeout waiting for data")

        stream_name, messages = result[0]
        chunk_id, fields = messages[0]
        return chunk_id, fields[b'data']

    async def get_chunk_by_range(self, last_id: Optional[str] = None) -> Optional[Tuple[str, bytes]]:
        """Read the next chunk from existing stream data (non-blocking)."""
        if not await self.redis.exists(self._stream_key):
            return None

        if last_id is not None:
            min_id = f'({last_id.decode() if isinstance(last_id, bytes) else last_id}'
        else:
            min_id = '0'

        chunks = await self.redis.xrange(self._stream_key, min=min_id, max='+', count=1)
        if not chunks:
            return None
        chunk_id, fields = chunks[0]
        return chunk_id, fields[b'data']

    async def set_event(self, event_name: str, expiry: float = None) -> None:
        """Publish an event."""
        expiry = expiry or self.EVENT_EXPIRY
        event_key = f'transfer:{self.transfer_id}:{event_name}'
        event_marker_key = f'{event_key}:marker'

        await self.redis.set(event_marker_key, '1', ex=int(expiry))
        await self.redis.publish(event_key, '1')

    async def wait_for_event(self, event_name: str, timeout: float = None) -> None:
        """Wait for an event using pub/sub and polling."""
        timeout = timeout or self.EVENT_EXPIRY
        event_key = f'transfer:{self.transfer_id}:{event_name}'
        event_marker_key = f'{event_key}:marker'
        pubsub = self.redis.pubsub(ignore_subscribe_messages=True)

        async def poll_marker():
            while not await self.redis.exists(event_marker_key):
                await anyio.sleep(1)

        async def listen_for_message():
            await pubsub.subscribe(event_key)
            async for message in pubsub.listen():
                if message and message['type'] == 'message':
                    return

        try:
            with anyio.fail_after(timeout):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(poll_marker)
                    tg.start_soon(listen_for_message)
        except TimeoutError:
            self.error(f"Timeout waiting for event '{event_name}' after {timeout} seconds")
            raise
        finally:
            await pubsub.unsubscribe(event_key)
            await pubsub.aclose()

    async def set_metadata(self, metadata: str) -> None:
        """Store transfer metadata atomically."""
        challenge = random.randbytes(8)
        await self.redis.set(self._k_meta, challenge, nx=True)
        if await self.redis.get(self._k_meta) == challenge:
            await self.redis.set(self._k_meta, metadata, ex=self.METADATA_EXPIRY)
        else:
            raise KeyError("Metadata already set for this transfer")

    async def get_metadata(self) -> Optional[str]:
        """Get transfer metadata."""
        return await self.redis.get(self._k_meta)

    async def set_receiver_connected(self) -> bool:
        """Mark receiver as connected (atomic)."""
        return bool(await self.redis.set(self._k_receiver_connected, '1', ex=self.METADATA_EXPIRY, nx=True))

    async def is_receiver_connected(self) -> bool:
        """Check if receiver is connected."""
        return await self.redis.exists(self._k_receiver_connected) > 0

    async def set_completed(self) -> None:
        """Mark transfer as completed."""
        await self.redis.set(f'transfer:{self.transfer_id}:completed', '1', ex=self.METADATA_EXPIRY, nx=True)

    async def is_completed(self) -> bool:
        """Check if transfer is completed."""
        return await self.redis.exists(f'transfer:{self.transfer_id}:completed') > 0

    async def set_interrupted(self) -> None:
        """Mark transfer as interrupted."""
        await self.redis.set(f'transfer:{self.transfer_id}:interrupt', '1', ex=self.STATE_EXPIRY, nx=True)

    async def is_interrupted(self) -> bool:
        """Check if transfer was interrupted."""
        return await self.redis.exists(f'transfer:{self.transfer_id}:interrupt') > 0

    async def save_upload_progress(self, bytes_uploaded: int, last_chunk_id: str) -> None:
        """Save upload progress for resumption."""
        progress = UploadProgress(bytes_uploaded=bytes_uploaded, last_chunk_id=last_chunk_id)
        await self.redis.hset(self._progress_key, mapping=progress.to_redis())
        await self.redis.expire(self._progress_key, self.PROGRESS_EXPIRY)

    async def get_upload_progress(self) -> Optional[UploadProgress]:
        """Get upload progress."""
        data = await self.redis.hgetall(self._progress_key)
        return UploadProgress.from_redis(data) if data and b'bytes_uploaded' in data else None

    async def save_download_progress(self, bytes_downloaded: int, last_read_id: str) -> None:
        """Save download progress for resumption."""
        progress = DownloadProgress(bytes_downloaded=bytes_downloaded, last_read_id=last_read_id)
        await self.redis.hset(self._progress_key, mapping=progress.to_redis())
        await self.redis.expire(self._progress_key, self.PROGRESS_EXPIRY)

    async def get_download_progress(self) -> Optional[DownloadProgress]:
        """Get download progress."""
        data = await self.redis.hgetall(self._progress_key)
        return DownloadProgress.from_redis(data) if data and b'bytes_downloaded' in data else None

    async def set_sender_state(self, state: ClientState) -> None:
        """Set sender state."""
        await self.redis.hset(self._state_key, 'sender', int(state))
        await self.redis.expire(self._state_key, self.STATE_EXPIRY)

    async def get_sender_state(self) -> Optional[ClientState]:
        """Get sender state."""
        state = await self.redis.hget(self._state_key, 'sender')
        return ClientState(int(state)) if state else None

    async def set_receiver_state(self, state: ClientState) -> None:
        """Set receiver state."""
        await self.redis.hset(self._state_key, 'receiver', int(state))
        await self.redis.expire(self._state_key, self.STATE_EXPIRY)

    async def get_receiver_state(self) -> Optional[ClientState]:
        """Get receiver state."""
        state = await self.redis.hget(self._state_key, 'receiver')
        return ClientState(int(state)) if state else None

    async def cleanup(self) -> int:
        """Clean up all transfer-related keys from Redis."""
        challenge = random.randbytes(8)
        await self.redis.set(self._k_cleanup, challenge, ex=self.CLEANUP_LOCK_EXPIRY, nx=True)
        if await self.redis.get(self._k_cleanup) != challenge:
            return 0

        keys_to_delete = {self._stream_key, self._progress_key, self._state_key}
        pattern = f'transfer:{self.transfer_id}:*'

        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern)
            keys_to_delete |= set(keys)
            if cursor == 0:
                break

        if keys_to_delete:
            self.debug(f"Cleaning up {len(keys_to_delete)} keys")
            return await self.redis.delete(*keys_to_delete)
        return 0