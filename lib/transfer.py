import asyncio
import weakref
from dataclasses import dataclass
from typing import AsyncIterator, Optional
from fastapi import Request, WebSocketException


@dataclass
class File:
    size: int
    name: Optional[str] = None
    content_type: Optional[str] = None


class FileTransfer:

    instances = weakref.WeakValueDictionary()

    def __init__(self, uid: str, file: File):
        self.uid = uid
        self.file = file
        self.queue = asyncio.Queue(16)
        self.client_connected = asyncio.Event()
        self.transfer_complete = asyncio.Event()

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
    def create_transfer(cls, uid: str, file: File):
        transfer = cls(uid, file)
        cls.instances[uid] = transfer
        return transfer

    @classmethod
    def get(cls, uid: str):
        if transfer := cls.instances.get(uid):
            return transfer
        raise KeyError(f"FileTransfer '{uid}' not found.")

    def get_file_info(self):
        return self.file.name, self.file.size, self.file.content_type

    async def transfer(self, stream: AsyncIterator[bytes]):
        try:
            async for chunk in stream:
                if not chunk:
                    print(f"⇑ {self.uid} ⇑ - Received empty chunk, ending upload.")
                    break
                await self.queue.put(chunk)

        except WebSocketException as e:
            print(f"⇑ {self.uid} ⇑ - Client disconnected during upload.")

        await self.queue.put(None)
        await self.transfer_complete.wait()

    async def receive(self):
        while True:
            chunk = await self.queue.get()
            if chunk is not None:
                yield chunk
            else:
                print(f"⇓ {self.uid} ⇓ - No more chunks to receive.")
                break

        self.transfer_complete.set()
        print(f"⇓ {self.uid} ⇓ - Transfer complete, notified all waiting tasks.")

    def __del__(self):
        print(f"Deleting transfer '{self.uid}'. {len(self.instances) - 1} transferes remaining.")
