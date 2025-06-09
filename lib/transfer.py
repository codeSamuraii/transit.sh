import json
import redis
import asyncio
from dataclasses import dataclass, asdict
from typing import AsyncIterator, Optional, Dict, Any
from fastapi import Request, WebSocketException


@dataclass(frozen=True)
class File:
    size: int
    name: Optional[str] = None
    content_type: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> 'File':
        return cls(**json.loads(data))


class FileTransfer:

    store: Optional[redis.Redis] = None

    def __init__(self, uid: str, file: File):
        self.uid = uid
        self.file = file
        self.get_redis()
        self.r_queue = f'transfer:{uid}:queue'

    @classmethod
    def get_redis(cls) -> redis.Redis:
        if cls.store is None:
            from app import redis_client
            cls.store = redis_client
        return cls.store

    @staticmethod
    def get_file_from_request(request: Request) -> File:
        file = File(
            name=request.path_params.get('filename'),
            size=int(request.headers.get('content-length') or 1),
            content_type=request.headers.get('content-type')
        )
        return file

    @staticmethod
    def get_file_from_header(header: dict) -> File:
        file = File(
            name=header['file_name'].encode('ascii', 'replace').decode(),
            size=int(header['file_size']),
            content_type=header['file_type']
        )
        return file

    @classmethod
    async def create(cls, uid: str, file: File):
        transfer = cls(uid, file)
        await transfer.store.set(
            f'transfer:{uid}:metadata',
            file.to_json(),
            ex=1800
        )

        return transfer

    @classmethod
    async def get(cls, uid: str):
        from app import redis_client
        metadata_json = await redis_client.get(f'transfer:{uid}:metadata')
        if not metadata_json:
            raise KeyError(f"FileTransfer '{uid}' not found.")

        file = File.from_json(metadata_json)
        return cls(uid, file)


    def get_file_info(self):
        return self.file.name, self.file.size, self.file.content_type

    async def get_from_queue(self, timeout: float = 30.0) -> bytes:
        result = await self.store.brpop(self.r_queue, timeout=timeout)
        if not result:
            raise asyncio.TimeoutError("Timeout waiting for data")

        _, chunk = result
        return chunk

    async def put_in_queue(self, chunk: bytes):
        while await self.store.llen(self.r_queue) >= 16:
            await asyncio.sleep(0.5)

        await self.store.lpush(self.r_queue, chunk)
        await self.store.expire(self.r_queue, 30, gt=True)


    async def set_event(self, event_name: str):
        await self.store.set(f'transfer:{self.uid}:{event_name}', '1', nx=True, ex=300)

    async def wait_for_event(self, event_name: str, timeout: float = 300.0):
        async def _wait(_uid, _evt):
            while await self.store.get(f'transfer:{_uid}:{_evt}') is None:
                await asyncio.sleep(0.5)
        await asyncio.wait_for(_wait(self.uid, event_name), timeout=timeout)

    async def collect_upload(self, stream: AsyncIterator[bytes]):
        try:
            while True:
                chunk = await anext(stream, None)
                if not chunk:
                    print(f"{self.uid} △ Received empty chunk, ending upload...")
                    break
                await asyncio.wait_for(self.put_in_queue(chunk), 30.0)

        except WebSocketException as e:
            print(f"{self.uid} △ Client disconnected during upload.")
            await self.cleanup()
            return

        await self.put_in_queue(b'')
        await self.wait_for_event('transfer_complete', timeout=3600)
        print(f"{self.uid} △ Received transfer complete signal.")

    async def supply_download(self):
        while True:
            try:
                chunk = await self.get_from_queue()
            except asyncio.TimeoutError:
                print(f"{self.uid} ▼ Timeout waiting for data after 20 seconds.")
                break
            if not chunk:
                print(f"{self.uid} ▼ No more chunks to receive.")
                break

            yield chunk

        await self.set_event("transfer_complete")
        print(f"{self.uid} ▼ Transfer complete, notified all waiting tasks.")
        await self.cleanup()

    async def cleanup(self):
        keys_to_delete = await self.store.keys(f'transfer:{self.uid}:*')
        if keys_to_delete:
            print(f"Removing {len(keys_to_delete)} keys for '{self.uid}' from store.")
            await self.store.delete(*keys_to_delete)
