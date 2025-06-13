import asyncio
import redis.asyncio as redis
from typing import Optional



class Store:
    """
    Redis-based store for file transfer queues and events.
    Handles data queuing and event signaling for transfer coordination.
    """

    _redis: Optional[redis.Redis] = None

    def __init__(self, transfer_id: str):
        self.transfer_id = transfer_id
        self.redis = self.get_redis()

        self._k_queue = self.key('queue')
        self._k_meta = self.key('metadata')
        self._cleanup = False

    @classmethod
    def get_redis(cls) -> redis.Redis:
        """Get the Redis client instance."""
        if cls._redis is None:
            from app import redis_client
            cls._redis = redis_client
        return cls._redis

    def key(self, name: str):
        """Get the Redis key for the provided name with this transfer."""
        return f'transfer:{self.transfer_id}:{name}'

    async def cleanup(self) -> int:
        """Remove all keys related to this transfer."""
        if self._cleanup:
            return 0

        self._cleanup = True
        await asyncio.sleep(4)  # Allow time for event notification

        pattern = self.key('*')
        keys_to_delete = set()

        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern)
            keys_to_delete |= set(keys)
            if cursor == 0:
                break

        if keys_to_delete:
            return await self.redis.delete(*keys_to_delete)
        return 0

    ## Queue operations ##

    async def _wait_for_queue_space(self, maxsize: int) -> None:
        while await self.redis.llen(self._k_queue) >= maxsize:
            await asyncio.sleep(0.5)

    async def put_in_queue(self, data: bytes, maxsize: int = 16, timeout: float = 30.0) -> None:
        """Add data to the transfer queue with backpressure control."""
        await asyncio.wait_for(self._wait_for_queue_space(maxsize), timeout=timeout)
        await self.redis.lpush(self._k_queue, data)

    async def get_from_queue(self, timeout: float = 30.0) -> bytes:
        """Get data from the transfer queue with timeout."""
        result = await self.redis.brpop([self._k_queue], timeout=timeout)
        if not result:
            raise asyncio.TimeoutError("Timeout waiting for data")

        _, data = result
        return data

    ## Event operations ##

    async def set_event(self, event_name: str, expiry: float = 300.0) -> None:
        """Set an event flag for this transfer."""
        event_key = self.key(event_name)
        await self.redis.set(event_key, '1', ex=int(expiry))

    async def wait_for_event(self, event_name: str, timeout: float = 300.0) -> None:
        """Wait for an event to be set for this transfer."""
        event_key = self.key(event_name)

        async def _wait():
            while await self.redis.get(event_key) is None:
                await asyncio.sleep(0.5)

        await asyncio.wait_for(_wait(), timeout=timeout)

    ## Metadata operations ##

    async def set_metadata(self, metadata: str, expiry: float = 3600.0) -> None:
        """Store transfer metadata."""
        await self.redis.set(self._k_meta, metadata, ex=int(expiry), nx=True)

    async def get_metadata(self) -> Optional[str]:
        """Retrieve transfer metadata."""
        return await self.redis.get(self._k_meta)
