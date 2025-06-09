import queue
import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Optional
from fastapi import Request, WebSocketException


@dataclass(frozen=True)
class File:
    size: int
    name: Optional[str] = None
    content_type: Optional[str] = None


class FileTransfer:

    manager = None
    store = None

    def __init__(self, uid: str, file: File):
        self.uid = uid
        self.file = file

        manager = FileTransfer._load_manager()
        self.queue = manager.Queue(16)
        self.client_connected = manager.Event()
        self.transfer_complete = manager.Event()

    @staticmethod
    def get_file_from_request(request: Request):
        file = File(
            name=request.path_params.get('filename'),
            size=int(request.headers.get('content-length') or 1),
            content_type=request.headers.get('content-type')
        )
        return file

    @staticmethod
    def get_file_from_header(header: dict):
        file = File(
            name=header['file_name'].encode('ascii', 'ignore').decode('utf-8'),
            size=int(header['file_size']),
            content_type=header['file_type']
        )
        return file

    @classmethod
    def _load_manager(cls):
        if cls.manager is None:
            from app import manager
            cls.manager = manager
        return cls.manager

    @classmethod
    def _load_store(cls):
        if cls.store is None:
            from app import store
            cls.store = store
        return cls.store

    @classmethod
    def create_transfer(cls, uid: str, file: File):
        store = cls._load_store()
        transfer = cls(uid, file)
        store[uid] = transfer
        return transfer

    @classmethod
    def get(cls, uid: str):
        store = cls._load_store()
        if transfer := store.get(uid):
            return transfer
        raise KeyError(f"FileTransfer '{uid}' not found.")

    def get_file_info(self):
        return self.file.name, self.file.size, self.file.content_type

    async def _get_from_queue(self) -> bytes:
        while True:
            try:
                chunk = self.queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.2)
                continue
            else:
                break

        return chunk

    async def get_from_queue(self, timeout: float = 10.0) -> bytes:
        return await asyncio.wait_for(self._get_from_queue(), timeout)

    async def _put_in_queue(self, chunk: bytes):
        while True:
            try:
                self.queue.put_nowait(chunk)
            except queue.Full:
                await asyncio.sleep(0.2)
                continue
            else:
                break

    async def put_in_queue(self, chunk: bytes, timeout: float = 10.0):
        return await asyncio.wait_for(self._put_in_queue(chunk), timeout)

    async def collect_upload(self, stream: AsyncIterator[bytes]):
        try:
            while True:
                chunk = await anext(stream, None)
                if not chunk:
                    print(f"⇑ {self.uid} ⇑ - Received empty chunk, ending upload.")
                    break
                await self.put_in_queue(chunk)

        except WebSocketException as e:
            print(f"⇑ {self.uid} ⇑ - Client disconnected during upload.")
            return

        await self.put_in_queue(None)
        self.transfer_complete.wait()

    async def supply_download(self):
        while True:
            try:
                chunk = await self.get_from_queue()
            except asyncio.TimeoutError:
                print(f"⇓ {self.uid} ⇓ - Timeout waiting for data after 20 seconds.")
                break

            if chunk is None:
                print(f"⇓ {self.uid} ⇓ - No more chunks to receive.")
                break

            yield chunk

        self.transfer_complete.set()
        print(f"⇓ {self.uid} ⇓ - Transfer complete, notified all waiting tasks.")

        print(f"Removing transfer '{self.uid}' from store.")
        store = self._load_store()
        del store[self.uid]
