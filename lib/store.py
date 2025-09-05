import random
import anyio
import redis.asyncio as redis
from redis.asyncio.client import PubSub
from typing import Optional, Annotated

from lib.logging import HasLogging, get_logger


class Store(metaclass=HasLogging, name_from='transfer_id'):
    """
    Redis-based store for file transfer queues and events.
    Handles data queuing and event signaling for transfer coordination.
    """

    redis_client: None | redis.Redis = None

    def __init__(self, transfer_id: str):
        self.transfer_id = transfer_id
        self.redis = self.get_redis()

        self._k_stream = self.key('stream')
        self._k_metadata = self.key('metadata')
        self._k_position = self.key('position')
        self._k_progress = self.key('progress')

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

    async def add_chunk(self, data: bytes) -> None:
        """Add chunk to stream."""
        # No maxlen limit - streams auto-expire after 5 minutes
        await self.redis.xadd(self._k_stream, {'data': data})

    async def stream_chunks(self, timeout_ms: int = 20000):
        """Stream chunks from last position."""
        position = await self.redis.get(self._k_position)
        last_id = position.decode() if position else '0'

        while True:
            result = await self.redis.xread({self._k_stream: last_id}, block=timeout_ms)
            if not result:
                raise TimeoutError("Stream read timeout")

            _, messages = result[0]
            for message_id, fields in messages:
                last_id = message_id
                await self.redis.set(self._k_position, last_id, ex=300)
                yield fields[b'data']

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

    async def set_metadata(self, metadata: str) -> None:
        """Store transfer metadata."""
        if not await self.redis.set(self._k_metadata, metadata, nx=True, ex=300):
            raise KeyError("Transfer already exists")

    async def get_metadata(self) -> str | None:
        """Get transfer metadata."""
        return await self.redis.get(self._k_metadata)

    async def save_progress(self, bytes_downloaded: int) -> None:
        """Save download progress."""
        await self.redis.set(self._k_progress, str(bytes_downloaded), ex=300)

    async def get_progress(self) -> int:
        """Get download progress."""
        progress = await self.redis.get(self._k_progress)
        return int(progress) if progress else 0

    async def cleanup(self) -> None:
        """Delete all transfer data."""
        pattern = self.key('*')
        cursor = 0
        keys = []

        while True:
            cursor, batch = await self.redis.scan(cursor, match=pattern)
            keys.extend(batch)
            if cursor == 0:
                break

        if keys:
            await self.redis.delete(*keys)
