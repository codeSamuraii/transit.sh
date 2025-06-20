import random
import asyncio
import redis.asyncio as redis
from typing import Optional, Coroutine

from lib.logging import get_logger


class Store:
    """
    Redis-based store for file transfer queues and events.
    Handles data queuing and event signaling for transfer coordination.
    """

    redis_client: None | redis.Redis = None

    def __init__(self, transfer_id: str):
        self.transfer_id = transfer_id
        self.redis = self.get_redis()
        self.log = get_logger(transfer_id)

        self._k_queue = self.key('queue')
        self._k_meta = self.key('metadata')
        self._k_cleanup = f'cleanup:{transfer_id}'

    @classmethod
    def get_redis(cls) -> redis.Redis:
        """Get the Redis client instance."""
        if cls.redis_client is None:
            from app import redis_client
            cls.redis_client = redis_client
        return cls.redis_client

    def key(self, name: str) -> str:
        """Get the Redis key for this transfer with the provided name."""
        return f'transfer:{self.transfer_id}:{name}'

    ## Queue operations ##

    async def _wait_for_queue_space(self, maxsize: int) -> None:
        while await self.redis.llen(self._k_queue) >= maxsize:
            await asyncio.sleep(0.5)

    async def put_in_queue(self, data: bytes, maxsize: int = 16, timeout: float = 10.0) -> None:
        """Add data to the transfer queue with backpressure control."""
        async with asyncio.timeout(timeout):
            await self._wait_for_queue_space(maxsize)
        await self.redis.lpush(self._k_queue, data)

    async def get_from_queue(self, timeout: float = 10.0) -> bytes:
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
        event_marker_key = f'{event_key}:marker'

        await self.redis.set(event_marker_key, '1', ex=int(expiry))
        await self.redis.publish(event_key, '1')

    async def wait_for_event(self, event_name: str, timeout: float = 300.0) -> None:
        """Wait for an event to be set for this transfer."""
        event_key = self.key(event_name)
        event_marker_key = f'{event_key}:marker'
        pubsub = self.redis.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(event_key)

        async def _poll_marker():
            while not await self.redis.exists(event_marker_key):
                await asyncio.sleep(1)
            self.log.debug(f">> POLL: Event '{event_name}' fired.")

        async def _listen_for_message():
            async for message in pubsub.listen():
                if message and message['type'] == 'message':
                    self.log.debug(f">> SUB : Received message for event '{event_name}'.")
                    return

        poll_marker = asyncio.wait_for(_poll_marker(), timeout=timeout)
        listen_for_message = asyncio.wait_for(_listen_for_message(), timeout=timeout)

        try:
            tasks = {
                asyncio.create_task(poll_marker, name=f'poll_marker_{event_name}_{self.transfer_id}'),
                asyncio.create_task(listen_for_message, name=f'listen_for_message_{event_name}_{self.transfer_id}')
            }
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()

        except asyncio.TimeoutError:
            self.log.error(f"Timeout waiting for event '{event_name}' after {timeout} seconds.")
            for task in tasks:
                task.cancel()
            raise

        finally:
            await pubsub.unsubscribe(event_key)
            await pubsub.close()
            await asyncio.gather(*tasks, return_exceptions=True)

    ## Metadata operations ##

    async def set_metadata(self, metadata: str) -> None:
        """Store transfer metadata."""
        if int (await self.redis.exists(self._k_meta)) > 0:
            raise KeyError(f"Metadata for transfer '{self.transfer_id}' already exists.")
        await self.redis.set(self._k_meta, metadata, nx=True)

    async def get_metadata(self) -> str | None:
        """Retrieve transfer metadata."""
        return await self.redis.get(self._k_meta)

    ## Transfer state operations ##

    async def set_completed(self) -> None:
        """Mark the transfer as completed."""
        await self.redis.set(f'completed:{self.transfer_id}', '1', ex=300, nx=True)

    async def is_completed(self) -> bool:
        """Check if the transfer is marked as completed."""
        return await self.redis.exists(f'completed:{self.transfer_id}') > 0

    async def set_interrupted(self) -> None:
        """Mark the transfer as interrupted."""
        await self.redis.set(f'interrupt:{self.transfer_id}', '1', ex=300, nx=True)
        await self.redis.ltrim(self._k_queue, 0, 0)

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

    async def cleanup(self) -> int:
        """Remove all keys related to this transfer."""
        if await self.cleanup_started():
            return 0

        pattern = self.key('*')
        keys_to_delete = set()

        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern)
            keys_to_delete |= set(keys)
            if cursor == 0:
                break

        if keys_to_delete:
            self.log.debug(f"- Cleaning up {len(keys_to_delete)} keys")
            return await self.redis.delete(*keys_to_delete)
        return 0
