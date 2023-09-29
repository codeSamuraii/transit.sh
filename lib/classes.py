import asyncio
from fastapi import Request
from dataclasses import dataclass


@dataclass
class File:
    name: str
    size: int
    content_type: str = None


class Duplex:

    instances = {}

    def __init__(self, stream, identifier: str, file: File, wait_for_client: bool):
        self.stream = stream
        self.identifier = identifier
        self.file = file
        self.queue = asyncio.Queue(1 if wait_for_client else 0)

        self.wait_for_client = wait_for_client
        self.client_connected = asyncio.Event()
    
    @staticmethod
    def get_upload_details(request: Request):
        stream = request.stream
        identifier = request.path_params.get('identifier')
        file = File(
            name=request.path_params.get('file_name'),
            size=request.headers.get('content-length'),
            content_type=request.headers.get('content-type')
        )
        return stream, identifier, file
    
    @classmethod
    def from_upload(cls, request: Request):
        stream, identifier, file = cls.get_upload_details(request)
        duplex = cls(stream, identifier, file, wait_for_client=True)
        cls.instances[identifier] = duplex
        return duplex

    @classmethod
    def from_upload_nowait(cls, request: Request):
        duplex = cls.from_upload(request)
        duplex.wait_for_client = False
        return duplex

    @classmethod
    def from_identifer(cls, identifier: str):
        if duplex := cls.instances.get(identifier):
            return duplex
        else:
            raise KeyError(f"Duplex '{identifier}' not found.")
    
    def get_file_info(self):
        return self.file.name, self.file.size, self.file.content_type
    
    async def transfer(self):
        bytes_read = 0

        if self.wait_for_client:
            print(f"Waiting for client to connect to '{self.identifier}'...")
            await self.client_connected.wait()
            print(f"Client connected to '{self.identifier}'.")

        async for chunk in self.stream():
            bytes_read += len(chunk)
            print(f"{len(chunk)} bytes read: {bytes_read}/{self.file.size}.", end='\r')
            await self.queue.put(chunk)
    
        await self.queue.put(None)
        return self.file.size, bytes_read
    
    async def receive(self):
        self.client_connected.set()
        while chunk := await self.queue.get():
            yield chunk

