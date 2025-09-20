import redis.asyncio as redis
from typing import Optional, AsyncIterator, Tuple
from lib.logging import HasLogging

class StreamStore(metaclass=HasLogging, name_from='transfer_id'):
    """Redis Stream-based storage for resumable file transfers."""

    def __init__(self, transfer_id: str, redis_client: redis.Redis):
        self.transfer_id = transfer_id
        self.redis = redis_client
        self._stream_key = f'stream:{transfer_id}'
        self._progress_key = f'progress:{transfer_id}'
        self._state_key = f'state:{transfer_id}'

    async def add_chunk(self, data: bytes, chunk_index: int = None) -> str:
        """Add a chunk to the stream and return the stream ID."""
        fields = {
            'data': data,
            'size': len(data),
            'index': chunk_index if chunk_index is not None else '*'
        }
        stream_id = await self.redis.xadd(self._stream_key, fields, maxlen=1000, approximate=True)
        return stream_id

    async def read_chunks(self, start_id: str = '0', count: Optional[int] = None, block: Optional[int] = None) -> list:
        """Read chunks from the stream starting from a specific ID."""
        params = {self._stream_key: start_id}
        result = await self.redis.xread(params, count=count, block=block)

        if result:
            stream_name, messages = result[0]
            return messages
        return []

    async def read_range(self, start_id: str = '-', end_id: str = '+', count: Optional[int] = None) -> list:
        """Read a range of chunks from the stream."""
        return await self.redis.xrange(self._stream_key, min=start_id, max=end_id, count=count)

    async def get_last_chunk_info(self) -> Optional[Tuple[str, dict]]:
        """Get the last chunk ID and data from the stream."""
        result = await self.redis.xrevrange(self._stream_key, count=1)
        if result:
            chunk_id, fields = result[0]
            return chunk_id, fields
        return None

    async def save_upload_progress(self, bytes_uploaded: int, last_chunk_id: str) -> None:
        """Save upload progress for resumption."""
        await self.redis.hset(self._progress_key, mapping={
            'bytes_uploaded': bytes_uploaded,
            'last_chunk_id': last_chunk_id
        })
        await self.redis.expire(self._progress_key, 3600)  # 1 hour TTL

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

    async def stream_exists(self) -> bool:
        """Check if the stream exists."""
        return await self.redis.exists(self._stream_key) > 0

    async def get_stream_length(self) -> int:
        """Get the number of entries in the stream."""
        return await self.redis.xlen(self._stream_key)

    async def find_chunk_by_byte_offset(self, byte_offset: int) -> Optional[Tuple[str, int]]:
        """Find the chunk ID and offset within chunk for a given byte position."""
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

        return None

    async def cleanup_stream(self) -> None:
        """Clean up the stream and associated keys."""
        await self.redis.delete(self._stream_key, self._progress_key, self._state_key)