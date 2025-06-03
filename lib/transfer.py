import asyncio
import weakref
from dataclasses import dataclass
from typing import AsyncIterator, Optional
from fastapi import Request, WebSocketDisconnect


@dataclass
class File:
    size: int
    name: Optional[str] = None
    content_type: Optional[str] = None


class FileTransfer:

    instances = weakref.WeakValueDictionary()

    def __init__(self, identifier: str, file: File):
        self.identifier = identifier
        self.file = file
        self.queue = asyncio.Queue(1)
        self.client_connected = asyncio.Event()
        self.transfer_complete = asyncio.Event()
        self.bytes_transferred = 0  # Track bytes actually transferred

    @staticmethod
    def get_file_from_request(request: Request):
        file = File(
            name=request.path_params.get('file_name'),
            size=int(request.headers.get('content-length') or 1),
            content_type=request.headers.get('content-type')
        )
        return file

    @staticmethod
    def get_file_from_header(header: dict):
        file = File(
            name=header['file_name'].encode('latin-1', errors='replace').decode('latin-1'),
            size=int(header['file_size']),
            content_type=header['file_type']
        )
        return file

    @classmethod
    def create_transfer(cls, identifier: str, file: File):
        transfer = cls(identifier, file)
        cls.instances[identifier] = transfer
        return transfer

    @classmethod
    def get(cls, identifier: str):
        if transfer := cls.instances.get(identifier):
            return transfer
        raise KeyError(f"FileTransfer '{identifier}' not found.")

    def get_file_info(self):
        return self.file.name, self.file.size, self.file.content_type

    async def transfer(self, stream: AsyncIterator[bytes]):
        try:
            bytes_read = 0
            async for chunk in stream:
                if not chunk:  # Empty chunk signals end of transfer
                    print(f"{self.identifier} - Received empty chunk, ending upload.")
                    break

                bytes_read += len(chunk)
                await self.queue.put(chunk)

        except WebSocketDisconnect:
            print(f"{self.identifier} - Client disconnected during upload.")

        await self.queue.put(None)
        await self.transfer_complete.wait()

    async def receive(self):
        bytes_sent = 0
        while True:
            chunk = await self.queue.get()
            if chunk is not None:
                bytes_sent += len(chunk)
                yield chunk
            else:
                print(f"{self.identifier} - No more chunks to receive. Total sent: {bytes_sent}")
                break
        self.bytes_transferred = bytes_sent
        self.transfer_complete.set()
        print(f"{self.identifier} - Transfer complete, notifying all waiting tasks. Final bytes: {bytes_sent}")

    def __del__(self):
        print(f"Deleting transfer '{self.identifier}'. {len(self.instances) - 1} transferes remaining.")
