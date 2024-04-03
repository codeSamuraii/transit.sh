import asyncio
import weakref
from fastapi import Request
from dataclasses import dataclass
from fastapi.responses import StreamingResponse


@dataclass
class File:
    size: int
    name: str = None
    content_type: str = None


class Duplex:

    instances = weakref.WeakValueDictionary()

    def __init__(self, stream, identifier: str, file: File, wait_for_client: bool):
        self.stream = stream
        self.identifier = identifier
        self.file = file
        self.queue = asyncio.Queue(1 if wait_for_client else 0)
        self.client_connected = asyncio.Event()
    
    @staticmethod
    def get_upload_details(request: Request):
        stream = request.stream
        identifier = request.path_params.get('identifier')
        file = File(
            name=request.path_params.get('file_name'),
            size=int(request.headers.get('content-length')),
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
    def from_identifer(cls, identifier: str):
        if duplex := cls.instances.get(identifier):
            return duplex
        else:
            raise KeyError(f"Duplex '{identifier}' not found.")
    
    def get_file_info(self):
        return self.file.name, self.file.size, self.file.content_type
    
    async def transfer(self):
        bytes_read = 0
    
        async for chunk in self.stream():
            bytes_read += len(chunk)
            await self.queue.put(chunk)
    
        await self.queue.put(None)

        while not self.queue.empty():
            await asyncio.sleep(0.5)
    
    async def receive(self):
        while True:
            chunk = await self.queue.get()
            if chunk is not None:
                yield chunk
            else:
                break
    
    def __del__(self):
        print(f"Deleting duplex '{self.identifier}'. {len(self.instances) - 1} duplexes remaining.")
