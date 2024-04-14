import asyncio
import weakref
from fastapi import Request
from dataclasses import dataclass


@dataclass
class File:
    size: int
    name: str = None
    content_type: str = None


class Duplex:

    instances = weakref.WeakValueDictionary()

    def __init__(self, identifier: str, file: File):
        self.identifier = identifier
        self.file = file
        self.queue = asyncio.Queue(1)
        self.client_connected = asyncio.Event()

    @staticmethod
    def get_file_from_request(request: Request):
        file = File(
            name=request.path_params.get('file_name'),
            size=int(request.headers.get('content-length')),
            content_type=request.headers.get('content-type')
        )
        return file

    @staticmethod
    def get_file_from_header(header: dict):
        file = File(
            name=header['file_name'],
            size=int(header['file_size']),
            content_type=header['file_type']
        )
        return file

    @classmethod
    def create_duplex(cls, identifier: str, file: File):
        duplex = cls(identifier, file)
        cls.instances[identifier] = duplex
        return duplex

    @classmethod
    def get(cls, identifier: str):
        if duplex := cls.instances.get(identifier):
            return duplex
        else:
            raise KeyError(f"Duplex '{identifier}' not found.")

    def get_file_info(self):
        return self.file.name, self.file.size, self.file.content_type

    async def wait_for_empty_queue(self, seconds=600):
        while not self.queue.empty() and seconds > 0:
            await asyncio.sleep(1)
            seconds -= 1

    async def transfer(self, stream):
        bytes_read = 0

        async for chunk in stream:
            bytes_read += len(chunk)
            await self.queue.put(chunk)

        await self.queue.put(None)
        await self.wait_for_empty_queue()
        return bytes_read

    async def receive(self):
        while True:
            chunk = await self.queue.get()
            if chunk is not None:
                yield chunk
            else:
                break

    def __del__(self):
        print(f"Deleting duplex '{self.identifier}'. {len(self.instances) - 1} duplexes remaining.")
